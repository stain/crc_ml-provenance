# Histopat Provenanace

This repository contains data, code, and other supplementary documents demonstrating application of the Provenance Backbone concept and underlying provenance model to a use case from digital pathology domain. Content of this repository consists of two parts:

1.  A textual description of the use case and application of the proposed provenance model to document the use case (“Supplementary text” folder),
2.	Implementation of provenance generation for computational parts of the use case, which is a machine learning (ML) workflow used for cancer detection research. The repository contains a portion of the ML workflow necessary to run the example. The implementation is described in this readme.


## The Machine Learning Workflow

The ML workflow is implemented as a set of python scripts, and consists of units called Experiments. An Experiment defines a logic of a job to be run using a configuration file . A configuration file is a nested JSON file describing:

- **Definitions** - defining what components (Data, Generator, Model, Callbacks, etc) are to be used in the experiment, and
- **Configurations** - defining parameters of the components.

Sample configuration files can be found in `rationai/config/` directory. The workflow can be run using the provided Makefile files:

### Slide conversion (xml_annot_patcher.py)

The preprocessing script prepares the WSIs to be processed by the ML workflow – splits the WSIs into two datasets and partitions each WSI into smaller regions, called patches, which are filtered and labeled. This preprocessing script  can process several directories of WSIs using the openslide-python package. Each slide is the processed in the following manner:

1. A binary background mask is generated using Otsu's Thresholding method.
2. If an XML annotation file is provided a binary label mask is generated by drawing polygons on a canvas. 
3. A sliding window technique is then applied on a background mask to generate patches. If a patch contains less tissue than a pre-defined threshold, the patch is discarded.
4. If a patch is not filtered by a background filter, it is assigned label according to the binary label mask.
5. Information about the patch (coordinates, label) is the added to a pandas table.
6. After all patches of a slide are processed, slide metadata (slide filepath, annotation filepath, etc) are added to the pandas table and the entire table is inserted into an index file (pandas HDFStore file).

```
make -f Makefile.convert run \
CONFIG_FILE=rationai/config/prov_converter_config.json
```

### Training (slide_train.py)

Training script implements the ML model training. The training script first splits the training set represented as an index file into two disjunct sets: training set and validation set. For both the training and the validation set a Generator is constructed. The generator behaves as following:

1. A sampling structure  is built from the contents of an index file.
2. During the training, the Generator samples a patch entry from the Sampler and passes it to an Extractor.
3. The Extractor accesses the appropriate slide and extracts an RGBA image from the coordinates within the sampled entry.
4. The extracted image is then augmented (if necessary) and normalized before being passed back to the Generator.
5. The Generator repeats this process for each sampled entry in a batch before passing the batch to the Model.

During the training the model repeatedly alternates between two modes:

- **Training mode** - the model updates its own parameters (weights) based on how well it manages to predict a correct label for the patches.

- **Validation mode** - the model tracks its performance on the validation dataset, which has not been provided to the ML model before. It uses this information to create periodic checkpoints on every improvement or to stop the training process prematurely.

```
make -f Makefile.experiment setup train \
TRAIN_CONFIG=rationai/config/prov_train_config.json \
EID_PREFIX=PROV-TRAIN
```

### Predictions (slide_test.py)

The script loads a previously trained model and executes it to create predictions for test slides (slides used neither for training nor validation of the model). The predictions for each slide are appended to its corresponding table as to new column and saved to disk as a new predictions HDFStore file.

```
make -f Makefile.experiment setup test \
TEST_CONFIG=rationai/config/prov_test_config.json \
EID_PREFIX=PROV-PREDICT
```

### Eval (slide_eval.py)

During evaluation Evaluator objects are used to calculate metrics of interest (Accuracy, Precision, Recall, etc). Generator uses different Extractor during evaluation. Instead of accessing slides and retrieving images, the Extractor retrieves only those columns from the HDFStore tables that are required by the Evaluators.

```
make -f Makefile.experiment setup eval \
EVAL_CONFIG=rationai/config/prov_eval_config.json \
EID_PREFIX=PROV-EVAL
```

---

To run all steps (training, prediction and evaluation) run the following command:

```
make -f Makefile.experiment run \
TRAIN_CONFIG=rationai/config/prov_train_config.json \
TEST_CONFIG=rationai/config/prov_test_config.json \
EVAL_CONFIG=rationai/config/prov_eval_config.json \
EID_PREFIX=PROV
```

Each makefile call creates a new experiment directory `<EID_PREFIX>-<EID_HASH>`, where `EID_PREFIX` can be set during the Makefile call for easier experiment identification, and `EID_HASH` is generated randomly to minimze experiment overwriting.

## Provenance Generation

Due to the heavy focus on configuration-driven approach a significant portion of the experiments can be documented by a provenance by defining the inputs (configuration file), the function (source code) and the outputs (output files). The configuration file details the modules and parameters used, whilst the source code defines the logic of individual modules and functions used. In our example, in the generated provenance documentation the source code is expressed as a git commit hash. For deterministic processes this is usually enough. 

In case we use a module with randomness (e.g. data splitting, data sampling) we need to retrieve and store the results of these random operations. For this purpose we have decided to use a simple logging approach. We export key-value pairs of interest into a structured JSON log to be subsequently processed by a provenanace generation script.

- **Preprocessing** - no special logging is needed as the entire process is fully deterministic. As such only the configuration file, github repository URL, and the output file are necessary for provenanace generation. Only a hashed content of the output dataset will be presented in the final provenance.

- **Training** - in order to validate reproducibility of an experiment we log the states of the following objects: Datasource (hashed content of data split sets), Generator (hashed sampled entries for each epoch), Model (training and validation metric at the end of an epoch; checkpoints). 

- **Predictions** - fully deterministic process. We log the inputs (model checkpoint and dataset), logic (git commit hash) and outputs (HDF5 file with predictions).

- **Evaluations** - fully deterministic process. We log the inputs (model checkpoint and dataset), logic (git commit hash) and outputs (results of Evaluators). 

> The corresponding log files and configuration files of an exemplary run of the ML workflow can be found in [outputs/experiment_logs](/outputs/experiment_logs).

### Logging

During a run of an experiment, a structured JSON log is being constructed using a custom `SummaryWriter` object. Only a single copy with a given name can exist at any given time. Retrieveing a `SummaryWriter` object with the same name from multiple locations results in the same object similarly to standard `logging.Logger`. 

Any key-value pair that we wish to keep track of must be set using the `SummaryWriter` `.set()` or `.add()` functions. The utility package `rationai.utils.provenance` contains additional helpful functions, for example generating SHA256 hashes of pandas tables, pandas HDFStore, filepaths and directories.

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

In order to parse the logs and generate resulting provenance according to the proposed model, we can call the `Makefile.provenance` file.

**Provenance Graph Generation**

```
make -f Makefile.provenance run \
PREPROC_LOG=data/prov_preprocess.log \
TRAIN_LOG=experiments/8c85b9321e00eeac082da2c3/prov_train.log \
TEST_LOG=experiments/8c85b9321e00eeac082da2c3/prov_test.log \
EVAL_LOG=experiments/8c85b9321e00eeac082da2c3/prov_eval.log \
```

The result of this command are three provenance bundles depicted in PNG images: `prov-preprocessing`, `prov-training`, and `prov-evaluation`.

> The resulting provenance graphs serialized into a graphical format can be found in [outputs/provenance_graphs](outputs/provenance_graphs). The underlying library for provenance handling would enable serialization of provenance into PROV-O (RDF), PROV-XML and PROV-JSON formats.


