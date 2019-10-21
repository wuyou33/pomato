"""
This is the result processing
"""
import sys
import numpy as np
import pandas as pd
import logging
import json
import matplotlib.pyplot as plt

import tools
# pylint: disable-msg=E1101

class ResultProcessing():
    """Data Woker Class"""
    def __init__(self, data, opt_folder, opt_setup, grid=None):
        self.logger = logging.getLogger('Log.MarketModel.DataManagement.ResultData')
        self.data = data

        self.grid = grid
        result_folder_name = str(opt_folder).split("\\")[-1]
        self.result_folder = self.data.wdir.joinpath("data_output").joinpath(result_folder_name)
        if not self.result_folder.is_dir():
            self.result_folder.mkdir()

        for var in self.data.result_attributes["variables"]:
            setattr(self, var, pd.DataFrame())
        for var in self.data.result_attributes["dual_variables"]:
            setattr(self, var, pd.DataFrame())
        for var in self.data.result_attributes["infeasibility_variables"]:
            setattr(self, var, pd.DataFrame())

        # Add opt Set-Up to the results attributes
        self.data.result_attributes = {**self.data.result_attributes, **opt_setup}
        self.data.result_attributes["source"] = opt_folder
        self.load_results_from_folder(opt_folder)

        # set-up: dont show the graphs when created
        plt.ioff()

    def load_results_from_folder(self, folder):
        """Loading Results CSVs from results folder"""
        folder_name = str(folder).split("\\")[-1]
        self.logger.info("Loading Results from results folder %s", folder_name)

        for variable_type in ["variables", "dual_variables", "infeasibility_variables"]:
            for var in self.data.result_attributes[variable_type]:
                try:
                    setattr(self, var, pd.read_csv(str(folder.joinpath(f"{var}.csv"))))
                    self.data.result_attributes[variable_type][var] = True
                except FileNotFoundError:
                    self.logger.warning("%s not in results folder %s", var, folder_name)

        ## Manual setting of attributes:
        with open(str(folder.joinpath("misc_result.json")), "r") as jsonfile:
            self.data.result_attributes["objective"] = json.load(jsonfile)

        if self.data.options["optimization"]["gams"]:
            model_stat = self.data.result_attributes["objective"]["Solve Status"]
            model_stat_str = tools.gams_modelstat_dict(model_stat)
            self.data.result_attributes["objective"]["Solve Status"] = model_stat_str

        # self.data.result_attributes["objective"] = data["Objective Value"]
        self.data.result_attributes["model_horizon"] = list(self.INJ.t.unique())


    def price(self):
        """returns nodal electricity price"""
        eb_nodal = self.EB_nodal.copy()
        eb_nodal = pd.merge(eb_nodal, self.data.nodes.zone.to_frame(),
                            how="left", left_on="n", right_index=True)
        eb_nodal.loc[abs(eb_nodal.EB_nodal) < 1E-3, "EB_nodal"] = 0

        eb_zonal = self.EB_zonal.copy()
        eb_zonal.loc[abs(eb_zonal.EB_zonal) < 1E-3, "EB_zonal"] = 0

        price = pd.merge(eb_nodal, eb_zonal, how="left",
                         left_on=["t", "zone"], right_on=["t", "z"])

        price["marginal"] = -(price.EB_zonal + price.EB_nodal)
        return price[["t", "n", "z", "marginal"]]

    def commercial_exchange(self, timestep):
        """Returns commercial exchange"""
        exchange = self.EX[(self.EX.t == timestep)][["EX", "z", "zz"]]
        exchange.columns = ["values", "from_zone", "to_zone"]
        exchange = exchange.pivot(values="values", index="from_zone", columns="to_zone")
        return exchange

    def net_position(self):
        """ Returns NEX"""
        net_position = pd.DataFrame(index=self.EX.t.unique())
        for zone in self.data.zones.index:
            net_position[zone] = self.EX[self.EX.z == zone].groupby("t").sum() - \
                                 self.EX[self.EX.zz == zone].groupby("t").sum()
        return net_position

    def check_infeasibilities(self):
        """
        checks for infeasiblity variables in electricity/heat energy balance
        and line infeasibility variables
        returns nothing
        """
        numerics = ['int16', 'int32', 'int64', 'float16', 'float32', 'float64']
        for infeasibilities in self.data.result_attributes["infeasibility_variables"]:
            tmp = getattr(self, infeasibilities)
            for col in tmp.select_dtypes(include=numerics):
                if any(tmp[col] > 1e-3):
                    self.logger.warning("Infeasibilites in %s", col)

    def check_courtailment(self):
        """ Amount Curtailed by TS tech"""
        ts_option = self.data.options["optimization"]["plant_types"]["ts"]
        res_plants = self.data.plants[self.data.plants.plant_type.isin(ts_option)]

        gen = self.G
        ava = self.data.availability.copy()
        ava.columns = ["t", "p", "ava"]

        gen = gen[gen.p.isin(res_plants.index)]
        gen = pd.merge(gen, res_plants[["g_max"]], how="left", left_on="p", right_index=True)
        gen = pd.merge(gen, ava, how="left", on=["p", "t"])

        gen.ava.fillna(1, inplace=True)

        gen["ava_gen"] = gen.g_max*gen.ava
        gen["curt"] = gen.ava_gen - gen.G
        curtailment = gen["curt"].round(3).sum()
        self.logger.info("%s MWh curtailed in market model results!", curtailment)
        return gen

    def res_share(self):
        """return res share in dispatch"""

        res_plants = self.data.plants[self.data.plants.fuel.isin(["wind",
                                                                  "sun", "water",
                                                                  "biomass"])]
        gen = self.G
        gen_res = gen[gen.p.isin(res_plants.index)]
        res_share = gen_res.G.sum()/gen.G.sum()
        self.logger.info("Renewable share is %d %% in resulting dispatch!", {round(res_share*100, 2)})
        return res_share

    def default_plots(self, show_plot=False):
        """Set of Standard Plots"""
        if show_plot:
            plt.ion()

        generation = pd.merge(self.G, self.data.plants[["node", "fuel", "tech"]],
                              how="left", left_on="p", right_index=True)
        generation = pd.merge(generation, self.data.nodes.zone.to_frame(),
                              how="left", left_on="node", right_index=True)
        model_horizon = self.data.result_attributes["model_horizon"]

        # By Fuel
        fig, ax = plt.subplots()
        g_by_fuel = generation.groupby(["t", "fuel"], as_index=False).sum()
        g_by_fuel.pivot(index="t", columns="fuel",
                        values="G").plot.area(ax=ax,
                                              xticks=[x for x in range(0, len(model_horizon))],
                                              figsize=(20, 10), rot=45)
        ax.legend(loc='upper right')
        ax.margins(x=0)
        fig.savefig(str(self.result_folder.joinpath("gen_fuel.png")))

        # Aggregated example
        fig, ax = plt.subplots()
        g_by_fuel.groupby("fuel").sum().plot.pie(ax=ax, y="G", figsize=(20, 20),)
        ax.legend(loc='upper right')
        ax.margins(x=0)
        fig.savefig(str(self.result_folder.joinpath("gen_fuel_pichart.png")))

        # By Tech
        fig, ax = plt.subplots()
        g_by_tech = generation.groupby(["t", "tech"], as_index=False).sum()
        g_by_tech.pivot(index="t", columns="tech",
                        values="G").plot.area(ax=ax,
                                              xticks=[x for x in range(0, len(model_horizon))],
                                              figsize=(20, 10), rot=45)
        ax.legend(loc='upper right')
        ax.margins(x=0)
        fig.savefig(str(self.result_folder.joinpath("gen_tech.png")))

        # Renewables generation
        fig, ax = plt.subplots()
        res_gen = generation[generation.fuel.isin(["sun", "wind"])].groupby(["t", "fuel"],
                                                                            as_index=False).sum()
        res_gen.pivot(index="t", columns="fuel",
                      values="G").plot(ax=ax, xticks=[x for x in range(0, len(model_horizon))],
                                       figsize=(20, 10), rot=45)

        ax.legend(loc='upper right')
        ax.margins(x=0)
        fig.savefig(str(self.result_folder.joinpath("gen_res.png")))

        # Storage Generation, Demand and LEvel
        fig, ax = plt.subplots()
        stor_d = self.D_es.groupby(["t"], as_index=True).sum()
        stor_l = self.L_es.groupby(["t"], as_index=True).sum()
        stor_tech = ["reservoir", "psp"]
        stor_g = generation[generation.tech.isin(stor_tech)].groupby(["t"], as_index=True).sum()
        pd.concat([stor_d, stor_l, stor_g],
                  axis=1).plot(ax=ax,
                               xticks=[x for x in range(0, len(model_horizon))],
                               figsize=(20, 10), rot=45)

        ax.legend(loc='upper right')
        ax.margins(x=0)
        fig.savefig(str(self.result_folder.joinpath("storage.png")))

        # Close all Figures
        fig.clf()

    ######
    # Grid Analytics
    # - Load Flows
    def n_0_flow(self, timesteps=None):
        """Calculate N-0 Flows"""

        if not timesteps:
            self.logger.info("Calculateting N-0 Flows for the full model horizon")
            timesteps = self.data.result_attributes["model_horizon"]

        n_0_flows = pd.DataFrame(index=self.data.lines.index)
        for t in timesteps:
            n_0_flows[t] = np.dot(self.grid.ptdf, self.INJ.INJ[self.INJ.t == t].values)
        return n_0_flows

    def n_1_flow(self, timesteps=None, lines=None, outages=None, sensitivity=5e-2):
        """Line flows on lines (cb) under outages (co)
           input lines/outages list of line indices
           timesteps list of timestepts
           output DF[lines, outages, timesteps]
        """
        if not self.grid:
            self.logger.error("Grid not available in results object!")
            return None

        if (lines and not all([l in self.data.lines.index for l in lines])) \
            or (outages and not all([o in self.data.lines.index for o in outages])):
            self.logger.error("Not all CBs/COs are indices of lines!")
            return None

        if not timesteps:
            self.logger.info("Calculating N-1 Flows for the full model horizon")
            timesteps = self.data.result_attributes["model_horizon"]

        if not lines:
            self.logger.info("Using all lines from grid model as CBs")
            lines = list(self.grid.lines.index)

        use_lodf = False
        if not outages:
            self.logger.info("Using COs with a sensitivity of %d percent to CBs",
                             round(sensitivity*100, 2))
            use_lodf = True

        ptdf = [self.grid.ptdf]
        label_lines = list(self.grid.lines.index)
        label_outages = ["basecase" for i in range(0, len(self.grid.lines.index))]

        for line in self.grid.lines.index[self.grid.lines.contingency]:
            if use_lodf:
                outages = list(self.grid.lodf_filter(line, sensitivity))
            label_lines.extend([line for i in range(0, len(outages))])
            label_outages.extend(outages)

        # estimate size of array = nr_elements * bytes per element
        # (float64 + sep = 8 + 1) / (1024**2) MB
        estimate_size = len(label_lines)*len(self.grid.nodes.index)*(8 + 1)/(1024*1024)
        if estimate_size > 5000:
            raise Exception('Matrix N-1 PTDF MAtrix too large! Use a higher sensitivity!')


        for line in self.grid.lines.index[self.grid.lines.contingency]:
            if use_lodf:
                outages = list(self.grid.lodf_filter(line, sensitivity))
            tmp_ptdf = np.vstack([self.grid.create_n_1_ptdf_cbco(line, out) for out in outages])
            ptdf.append(tmp_ptdf)

        n_1_flows = pd.DataFrame()
        n_1_flows["cb"] = label_lines
        n_1_flows["co"] = label_outages

        ptdf = np.concatenate(ptdf).reshape(len(label_lines),
                                            len(list(self.grid.nodes.index)))
        for t in timesteps:
            n_1_flows[t] = np.dot(ptdf, self.INJ.INJ[self.INJ.t == t].values)

        return n_1_flows

    def overloaded_lines_n_0(self, timesteps=None):
        """
        Information about N-0 (over) Lineflows
        returns a DataFrame with respective info
        and timeseries of overloaded lines
        """
        if not timesteps:
            # if not specifie use full model horizon
            timesteps = self.data.result_attributes["model_horizon"]

        flows = self.n_0_flow(timesteps)

        rel_load_array = np.vstack([(abs(flows[t]))/self.data.lines.maxflow for t in timesteps]).T
        rel_load = pd.DataFrame(index=flows.index, columns=flows.columns,
                                data=rel_load_array)

        # Only those with over loadings
        n_0_load = rel_load[np.any(rel_load.values > 1.01, axis=1)]

        return_df = pd.DataFrame(index=n_0_load.index)
        cond = np.any(rel_load.values > 1.01, axis=1)
        return_df["# of overloads"] = np.sum(rel_load.values > 1.01, axis=1)[cond]
        return_df["avg load"] = n_0_load.mean(axis=1)

        return return_df, n_0_load

    def overloaded_lines_n_1(self, timesteps=None, sensitivity=5e-2):
        """
        Information about N-1 (over) Lineflows
        returns a DataFrame with respective info
        and timeseries of overloaded lines
        """
        if not timesteps:
            # if not specifie use full model horizon
            timesteps = self.data.result_attributes["model_horizon"]

        n_1_flow = self.data.results.n_1_flow(sensitivity=sensitivity)
        n_1_load = n_1_flow.copy()

        timesteps = self.data.result_attributes["model_horizon"]
        maxflow_values = self.grid.lines.maxflow[n_1_load.cb].values
        n_1_load.loc[:, timesteps] = n_1_flow.loc[:, timesteps].div(maxflow_values, axis=0).abs()

        # 2% overload as tolerance
        n_1_overload = n_1_load[~(n_1_load[timesteps] <= 1.02).all(axis=1)]
        return_df = n_1_overload[["cb", "co"]].copy()
        return_df["# of overloads"] = np.sum(n_1_overload[timesteps] > 1, axis=1).values
        return_df["# of COs"] = 1
        return_df = return_df.groupby("cb").sum()
        return_df["avg load"] = n_1_overload.groupby(by=["cb"]).mean().mean(axis=1).values

        cond = n_1_overload.co == "basecase"
        bool_values = [line in n_1_overload.cb[cond].values for line in return_df.index]
        return_df["basecase overload"] = bool_values

        sys.stdout.write("\n")
        return return_df, n_1_overload