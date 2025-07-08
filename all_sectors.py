import pandas as pd
import sqlite3
from setup import config

def build():
    conn = sqlite3.connect(config.database_file)
    curs = conn.cursor()

    df_cef: pd.DataFrame = pd.read_csv('input_files/end-use-demand-2023.csv')

    # Map CEF indexing to CANOE indexing
    region_map = dict()
    for region, row in config.regions.iterrows():
        if row['include']: region_map[row['cef_region']] = region
    commodity_map = dict()
    for comm, row in config.commodities.iterrows():
        if row['include']: commodity_map[row['cef_fuel']] = comm
    sector_map = dict()
    technology_map = dict()
    for tag, row in config.sectors.iterrows():
        if row['include']:
            sector_map[row['cef_sector']] = tag
            technology_map[row['cef_sector']] = row['tech']

    # Filter data
    df_cef = df_cef[
        (df_cef['Scenario'] == config.params['scenario'])
        & df_cef['Sector'].isin(sector_map.keys())
        & df_cef['Region'].isin(region_map.keys())
        & df_cef['Variable'].isin(commodity_map.keys())
        & df_cef['Year'].isin(config.model_periods)
    ]

    # Convert to CANOE indexing
    df_cef['region'] = df_cef['Region'].map(region_map, na_action='ignore')
    df_cef['tag'] = df_cef['Sector'].map(sector_map, na_action='ignore')
    df_cef['tech'] = df_cef['Sector'].map(technology_map, na_action='ignore')
    df_cef['comm'] = df_cef['Variable'].map(commodity_map, na_action='ignore')
    df_cef['tech'] = df_cef['tag'] + '_' + df_cef['tech']
    df_cef['comm'] = df_cef['tag'] + '_' + df_cef['comm']
    df_cef['year'] = df_cef['Year']

    # Convert energy units and filter out tiny values
    df_cef['value'] = df_cef['Value'] * config.params['conversion_factor']
    df_cef['value'] = df_cef['value'].mask(df_cef['value'] < config.params['zero_thresh'], other=0)

    # Remove any zero-flow streams
    df = df_cef.groupby(['region','tech','comm'])['value']
    to_drop = set()
    for idx, _df in df:
        if _df.sum() == 0: to_drop.add(idx)
    df_cef = df_cef.set_index(['region','tech','comm'])
    df_cef = df_cef[~df_cef.index.isin(to_drop)]

    # Group indices nicely
    df_cef = df_cef.reset_index()
    df_cef = df_cef.set_index(['region','tech','year','comm'])['value']
    df_cef = df_cef.sort_index()

    print(df_cef)
    df_cef.to_csv('test.csv')

    # Add sectors
    for tech in df_cef.index.get_level_values('tech').unique():
        sector = config.sectors.loc[tech.split("_")[0]]
        
        # Technology
        sql = (
            'REPLACE INTO Technology(tech, flag, sector, unlim_cap, description) '
            f'VALUES("{tech}", "p", "{sector["sector"]}", 1, "{sector["tech_desc"]}")'
        )
        curs.execute(sql)

        # SectorLabel
        sql = (
            'REPLACE INTO SectorLabel(sector) '
            f'VALUES("{sector["sector"]}")'
        )
        curs.execute(sql)

        # Commodity (demand comm)
        dem_comm = f'{tech.split("_")[0]}_D_{tech.split("_")[1].lower()}'
        sql = (
            'REPLACE INTO Commodity(name, flag, description) '
            f'VALUES("{dem_comm}", "a", "({config.params["energy_units"]}) {sector["sector"]} energy demand")'
        )
        curs.execute(sql)

    # Add commodities
    for comm in df_cef.index.get_level_values('comm').unique():
        sector = config.sectors.loc[comm.split("_")[0]]
        commodity = config.commodities.loc[comm.split("_")[1]]
        if isinstance(commodity, pd.DataFrame):
            commodity = commodity.iloc[0]
        desc = f'{commodity["description"]} ({sector["sector"]})'

        # Commodity (fuel comms)
        sql = (
            'REPLACE INTO Commodity(name, flag, description) '
            f'VALUES("{comm}", "a", "({config.params["energy_units"]}) {desc}")'
        )
        curs.execute(sql)

    # 2025 vintage processes for all techs
    for region, tech, comm in df_cef.xs(2025, level='year').index:
        dem_comm = f'{tech.split("_")[0]}_D_{tech.split("_")[1].lower()}'

        # Efficiency
        sql = (
            'REPLACE INTO Efficiency(region, input_comm, tech, vintage, output_comm, efficiency) '
            f'VALUES("{region}", "{comm}", "{tech}", 2025, "{dem_comm}", 1.0)'
        )
        curs.execute(sql)

    # Demands for each sector
    for (region, tech, period), demand in df_cef.reset_index().groupby(['region','tech','year']):

        demand = demand.round(config.params['decimal_places'])
        dem_tot = round(demand['value'].sum(), config.params['decimal_places'])
        dem_comm = f'{tech.split("_")[0]}_D_{tech.split("_")[1].lower()}'

        # Demand
        sql = (
            'REPLACE INTO Demand(region, period, commodity, demand, units) '
            f'VALUES("{region}", {period}, "{dem_comm}", {dem_tot}, "{config.params["energy_units"]}")'
        )
        curs.execute(sql)
        
        # LimitTechInputSplitAnnual
        for _, row in demand.iterrows():
            prop = round(row['value'] / dem_tot, 2)
            if prop < config.params['split_thresh']: continue
            sql = (
                'REPLACE INTO LimitTechInputSplitAnnual(region, period, input_comm, tech, operator, proportion) '
                f'VALUES("{region}", {period}, "{row["comm"]}", "{tech}", "le", {prop})'
            ) # <= should, in theory, be least likely to cause infeasibility / numerical issues?
            curs.execute(sql)


    # DSDs (only affects electricity)

    # Tech input splits

    conn.commit()


if __name__ == "__main__":
    build()