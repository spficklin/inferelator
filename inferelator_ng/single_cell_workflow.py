"""
Run Single Cell Network Inference
"""
import pandas as pd
import types

from inferelator_ng.tfa import TFA
from inferelator_ng import utils
from inferelator_ng import tfa_workflow
from inferelator_ng import elasticnet_python
from inferelator_ng import single_cell
from inferelator_ng import default


class SingleCellWorkflow(object):
    # Gene list
    gene_list_file = default.DEFAULT_GENE_LIST_FILE
    gene_list = None
    gene_list_index = default.DEFAULT_GENE_LIST_INDEX_COLUMN

    # Single-cell expression data manipulations
    count_minimum = default.DEFAULT_COUNT_MINIMUM  # float
    expression_matrix_columns_are_genes = default.DEFAULT_EXPRESSION_DATA_IS_SAMPLES_BY_GENES  # bool
    extract_metadata_from_expression_matrix = default.DEFAULT_EXTRACT_METADATA_FROM_EXPR  # bool
    expression_matrix_metadata = default.DEFAULT_EXPRESSION_MATRIX_METADATA  # str

    # Preprocessing workflow holder
    preprocessing_workflow = list()

    # TFA modification flags
    modify_activity_from_metadata = default.DEFAULT_MODIFY_TFA_FROM_METADATA
    metadata_expression_lookup = default.DEFAULT_METADATA_FOR_TFA_ADJUSTMENT
    gene_list_lookup = default.DEFAULT_GENE_LIST_LOOKUP_COLUMN

    def startup_run(self):

        # If the metadata is embedded in the expression matrix, monkeypatch a new read_metadata() function in
        # to properly extract it
        if self.extract_metadata_from_expression_matrix:
            def read_metadata(self):
                self.meta_data = self.expression_matrix.loc[:, self.expression_matrix_metadata].copy()
                self.expression_matrix = self.expression_matrix.drop(self.expression_matrix_metadata, axis=1)

            self.read_metadata = types.MethodType(read_metadata, self)

        # Load the usual data files for inferelator regression
        self.get_data()

    def startup_finish(self):
        # If the expression matrix is [G x N], transpose it for preprocessing
        if not self.expression_matrix_columns_are_genes:
            self.expression_matrix = self.expression_matrix.transpose()

        # Filter expression and priors to align
        self.single_cell_normalize()
        self.filter_expression_and_priors()
        self.compute_activity()

    def filter_expression_and_priors(self):
        # Transpose the expression matrix to convert from [N x G] to [G x N]
        self.expression_matrix = self.expression_matrix.transpose()

        # If gene_list_file is set, read a list of genes in and then filter the expression and priors to this list
        if self.gene_list_file is not None:
            self.read_genes()
            genes = self.gene_list[self.gene_list_index]
            self.expression_matrix = self.expression_matrix.loc[self.expression_matrix.index.intersection(genes)]
            self.priors_data = self.priors_data.loc[self.priors_data.index.intersection(genes)]

        # Only keep stuff from the expression matrix that's got counts
        self.expression_matrix = self.expression_matrix.loc[~(self.expression_matrix.sum(axis=1) == 0)]

        self.align_priors_and_expression()

    def align_priors_and_expression(self):
        # Make sure that the priors align to the expression matrix
        self.priors_data = self.priors_data.reindex(index=self.expression_matrix.index).fillna(value=0)

        # Trim to the tf_names list
        tf_keepers = list(set(self.tf_names).intersection(set(self.priors_data.columns.tolist())))
        self.priors_data = self.priors_data.loc[:, tf_keepers]

    def single_cell_normalize(self):
        """
        Single cell normalization. Requires expression_matrix to be all numeric, and to be [N x G]
        :return:
        """

        self.expression_matrix, self.meta_data = single_cell.filter_genes_for_count(self.expression_matrix,
                                                                                    self.meta_data,
                                                                                    count_minimum=self.count_minimum)

        if self.expression_matrix.isnull().values.any():
            raise ValueError("NaN values are present prior to normalization in the expression matrix")

        for sc_function, sc_kwargs in self.preprocessing_workflow:
            sc_kwargs['random_seed'] = self.random_seed
            self.expression_matrix, self.meta_data = sc_function(self.expression_matrix, self.meta_data, **sc_kwargs)

        if self.expression_matrix.isnull().values.any():
            raise ValueError("NaN values have been introduced into the expression matrix by normalization")

    def read_genes(self):

        with self.input_path(self.gene_list_file) as genefh:
            self.gene_list = pd.read_table(genefh, **self.file_format_settings)

    def compute_activity(self):
        """
        Compute Transcription Factor Activity
        """
        utils.Debug.vprint('Computing Transcription Factor Activity ... ')
        TFA_calculator = TFA(self.priors_data, self.expression_matrix, self.expression_matrix)
        self.design = TFA_calculator.compute_transcription_factor_activity()
        self.response = self.expression_matrix
        self.expression_matrix = None

        if self.modify_activity_from_metadata:
            self.apply_metadata_to_activity()

    def apply_metadata_to_activity(self):
        """
        Set design values according to metadata
        :return:
        """

        utils.Debug.vprint('Modifying Transcription Factor Activity ... ')

        # Get the genotypes from the metadata and map them to expression data names
        self.meta_data[self.metadata_expression_lookup] = self.meta_data[self.metadata_expression_lookup].str.upper()
        genotypes = self.meta_data[self.metadata_expression_lookup].unique().tolist()
        if self.gene_list is not None:
            genes = self.gene_list.loc[self.gene_list[self.gene_list_lookup].isin(genotypes), :]
        else:
            genes = self.design.index.isin(genotypes)

        # Convert the dataframe into a dict that can be used with pd.df.map()
        gene_map = dict(zip(genes[self.gene_list_lookup].tolist(), genes[self.gene_list_index].tolist()))

        # Replace the genotypes with the gene name to modify
        self.meta_data[self.metadata_expression_lookup] = self.meta_data[self.metadata_expression_lookup].map(gene_map)

        # Map the replacement function back into the design matrix
        for idx, row in self.meta_data.iterrows():
            if pd.isnull(row[self.metadata_expression_lookup]):
                continue
            new_value = self.tfa_adj_func(row[self.metadata_expression_lookup])
            self.design.loc[row[self.metadata_expression_lookup], idx] = new_value

    def tfa_adj_func(self, gene):
        return self.design.loc[gene, :].min()


class SingleCellBBSRWorkflow(SingleCellWorkflow, tfa_workflow.BBSR_TFA_Workflow):
    pass


class SingleCellMENWorkflow(SingleCellWorkflow, elasticnet_python.MEN_Workflow):
    pass
