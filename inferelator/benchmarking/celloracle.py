from inferelator import utils
from os import stat
from inferelator.single_cell_workflow import SingleCellWorkflow
from inferelator.regression.base_regression import _RegressionWorkflowMixin
import scanpy as sc
import numpy as np
import celloracle as co


class CellOracleWorkflow(SingleCellWorkflow):

    oracle = None
    oracle_imputation = True

    def startup_finish(self):
        """
        Skip inferelator preprocessing and do celloracle preprocessing

        As per https://github.com/morris-lab/CellOracle/issues/58
        """

        self.align_priors_and_expression()

        self.data.convert_to_float()

        adata = self.data._adata

        utils.Debug.vprint("Normalizing data {sh}".format(sh=adata.shape))

        sc.pp.filter_genes(adata, min_counts=1)
        sc.pp.normalize_per_cell(adata, key_n_counts='n_counts_all')

        adata.raw = adata
        adata.layers["raw_count"] = adata.raw.X.copy()

        utils.Debug.vprint("Scaling data")

        sc.pp.log1p(adata)
        sc.pp.scale(adata)

        utils.Debug.vprint("PCA Preprocessing")

        sc.tl.pca(adata, svd_solver='arpack')

        utils.Debug.vprint("Diffmap Preprocessing")

        sc.pp.neighbors(adata, n_neighbors=4, n_pcs=20)
        sc.tl.diffmap(adata)
        sc.pp.neighbors(adata, n_neighbors=10, use_rep='X_diffmap')

        utils.Debug.vprint("Clustering Preprocessing")

        sc.tl.louvain(adata, resolution=0.8)
        sc.tl.paga(adata, groups='louvain')
        sc.pl.paga(adata)
        sc.tl.draw_graph(adata, init_pos='paga', random_state=123)

        utils.Debug.vprint("Creating Oracle Object")

        # Restore counts
        adata.X = adata.layers["raw_count"].copy()

        # Set up oracle object
        oracle = co.Oracle()
        oracle.import_anndata_as_raw_count(adata=adata,
                                           cluster_column_name="louvain",
                                           embedding_name="X_pca")

        # Add prior
        oracle.addTFinfo_dictionary(self.reprocess_prior_to_base_GRN(self.priors_data))

        utils.Debug.vprint("Imputation Preprocessing")

        if self.oracle_imputation:
            n_comps = np.where(np.diff(np.diff(np.cumsum(oracle.pca.explained_variance_ratio_))>0.002))[0][0]
            n_cell = oracle.adata.shape[0]
            k = int(0.025*n_cell)

            oracle.knn_imputation(n_pca_dims=n_comps, k=k, balanced=True, b_sight=k*8,
                                b_maxl=k*4, n_jobs=4)

        # Pretend to do imputation
        else:
            oracle.adata.layers["imputed_count"] = oracle.adata.layers["normalized_count"].copy()

        self.oracle = oracle


    @staticmethod
    def reprocess_prior_to_base_GRN(priors_data):

        base_GRN = priors_data.copy()
        base_GRN.index.name = "Target"
        base_GRN = base_GRN.melt(ignore_index=False, var_name="Regulator").reset_index()
        base_GRN = base_GRN.loc[base_GRN['value'] != 0, :].copy()
        base_GRN.drop("value", axis=1, inplace=True)
        return {k: v["Regulator"].tolist() for k, v in base_GRN.groupby("Target")}


    @staticmethod
    def reprocess_co_output_to_inferelator_results(co_out):

        betas = [r.pivot(index='target', columns='source', values='-coef_mean').fillna(0) for k, r in co_out.items()]
        rankers = [r.pivot(index='target', columns='source', values='-logp').fillna(0) for k, r in co_out.items()]

        return betas, rankers


class CellOracleRegression(_RegressionWorkflowMixin):

    def run_regression(self):
        
        links = self.oracle.get_links(cluster_name_for_GRN_unit="louvain", alpha=10,
                                      verbose_level=0, test_mode=False)

        return self.reprocess_co_output_to_inferelator_results(links.links_dict)