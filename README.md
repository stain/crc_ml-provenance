# Histopat Provenanace

This repository-branch implements an example of a distributed provenance model generation. It is applied on a histopathological ML pipeline primarily used for cancer detection research.

The branch contains a portion of the Histopat pipeline necessary to run an example. 

## ML Pipeline

The pipeline works with units called Experiments. An Experiment defines a logic of a job to be run using a configuration file. A configuration file is a nested JSON file describing the following:

- **Definitions** defines what components (Data, Generator, Model, Callbacks, etc) are to be used in the experiment
- **Configurations** defines the parameters of the components

Sample configuration files can be found in `rationai/config/` directory. The pipeline can be run using the provided Makefile files:

**Slide conversion**

`make -f Makefile.convert run CONFIG_FILE=rationai/config/prov_converter_config.json`

**Experiments**

`make -f Makefile.experiment run TRAIN_CONFIG=rationai/config/prov_train_config.json TEST_CONFIG=rationai/config/prov_test_config.json EVAL_CONFIG=rationai/config/prov_eval_config.json EID_PREFIX=PROV`

alternatively, each experiment can be run individually

`make -f Makefile.experiment setup train TRAIN_CONFIG=rationai/config/prov_train_config.json EID_PREFIX=PROV-TRAIN` 

Each makefile call creates a new experiment directory `<EID_PREFIX>-<EID_HASH>` where `EID_PREFIX` can be set during the Makefile call for easier experiment identification and `EID_HASH` is generated randomly to minimze experiment overwriting.

### Preprocessing (xml_annot_patcher.py)

This preprocessing script is capable of processing several directories of histpathological slides using the `openslide-python` package. Each slide is the processed in the following manner:

1. A binary background mask is generated using Otsu's Thresholding method
2. If an XML annotation file is provided a binary label mask is generated by drawing polygons on a canvas.
3. A sliding window technique is then applied on a background mask to generate patches. If a patch contains less tissue than a pre-defined threshold, the patch is discarded.
4. If a patch is not filtered by a background filter it is assigned label according the binary label mask.
5. Information about the patch (coordinates, label) is the added to a pandas table.
6. After all patches of a slide are processed, slide metadata (slide filepath, annotation filepath, etc) are added to the pandas table and the entire table is inserted into an index file (pandas HDFStore file).

### Training (slide_train.py)

The training script first builds a data generator. A generator behaves as following:

1. A sampling structure is built from the contents of an index file.
2. During the training, the Generator samples a patch entry from the Sampler and passes it to an Extractor. 
3. The Extractor accesses the correct slide and extracts an RGBA image from the coordinates within the sampled entry. 
4. The extracted image is then augmented (if necessary) and normalized before being passed back to the Generator.
5. The Generator repeats this process for each sampled entry in a batch before passing the batch to the Model.

In the training script, the training slides in the index file are divided into two disjunct sets: training set and validation set.
For each set a Generator is constructed. During the training the model iterates between two modes:

- **Training mode** - the model updates its own parameters (weights) based on how well it manages to predict a correct label for the patches. 

- **Validation mode** - the model tracks its performance on a previously unseen dataset. It uses this information to create periodic checkpoints on every improvement or to stop the training process prematurely.

### Predictions (slide_test.py)

In this step we load a previously trained model using a checkpoint and make it create predictions for test slides (slides used neither for training nor validation). The predictions for each slide are appended to its corresponding table as to new column and saved to disk as a new predictions HDFStore file.

### Eval (slide_eval.py)

During evaluation Evaluator objects are used to calculate metrics of interest (Accuracy, Precision, Recall, etc). Generator during evaluation uses different Extractor. Instead of accessing slides and retrieving images the Extractor retrieves only those columns from the HDFStore tables that are required by the Evaluators.



## Provenance Generation

Due to the heavy focus on configuration-driven approach a significant portion of the experiments can be documented by a provenance by either processing to the configuration file or referring to a github repository. This leaves us with information that is a result of a random process (splitting, sampling, checkpoints). For this example we have decided for a simple approach of logging. We export key-value pairs of interest into a structured JSON log to be processed by a provenanace generation script.

- **Preprocessing** - no special logging is needed as the entire process is deterministic. As such only the configuration file, github repository URL, and the output file are necessary for provenanace generation. Only a hashed content of the output dataset will be presented in the final provenance.

- **Training** - in order to validate reproducibility of an experiment we log the states of the following objects: Datasource (hashed content of data split sets), Generator (hashed sampled entries for each epoch), Model (training and validation metric at the end of an epoch; checkpoints). 

- **Predictions** - similar to training we log the states of a Datasource and the output file (predictions).

- **Evaluations** - similar to training we log the states of a Datasource and the results of Evaluators.

### Logging

During a regular run of an experiment a structured JSON log is being constructed using a custom `SummaryWriter` object. Only a single copy with a given name can exist at any given time. Retrieveing a `SummaryWriter` object with the same name from multiple locations results in the same object similarly to standard `logging.Logger`. 

Any key-value pair that we wish to keep track of must be set using the `SummaryWriter` `.set()` or `.add()` functions. The utility package `rationai.utils.provenance` contains additional helpful functions for generating provenanace such as SHA256 hashing function for pandas tables, pandas HDFStore, filepaths and directories.

```
log = SummaryWriter.getLogger('provenance')
log.set('level1', 'level2, 'level3', value='value')
log.set('level1', 'key', value=5)
log.to_json(filepath)

# {
#     'level1': {
#         'level2': {
#             'level3': 'value'
#         },
#         'key': 5
#     }
# }

```



### Generation

In order to parse the logs and generate provenanace graph we can call the `Makefile.provenance` file.

**Provenance Graph Generation**

`make -f Makefile.provenance run TRAIN_LOG=experiments/8c85b9321e00eeac082da2c3/prov_train.log TEST_LOG=experiments/8c85b9321e00eeac082da2c3/prov_test.log EVAL_LOG=experiments/8c85b9321e00eeac082da2c3/prov_eval.log PREPROC_LOG=data/prov_preprocess.log`

The result of this command are three provenance graph PNG images: `prov-preprocessing`, `prov-training`, and `prov-evaluation`.


