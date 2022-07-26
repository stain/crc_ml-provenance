# Standard Imports
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict
from pathlib import Path
from typing import List
from typing import Optional
from typing import Tuple
from pydoc import locate

# Third-party Imports
import numpy as np
import openslide
from PIL import Image
from nptyping import NDArray
from openslide import OpenSlide

# Local Imports
from rationai.datagens.augmenters import ImgAugAugmenter
from rationai.datagens.samplers import SampledEntry
from rationai.utils.config import ConfigProto


class Extractor(ABC):

    @abstractmethod
    def __call__(self, sampled_entries: List[SampledEntry]):
        """Process sampled entries into valid network input (and output)"""


class OpenslideExtractor(Extractor):
    def __init__(self, augmenter: Optional[ImgAugAugmenter], *args, **kwargs):
        self.augmenter = augmenter

    def __call__(self, sampled_entries: List[SampledEntry]) -> Tuple[np.ndarray, np.ndarray]:
        """Converts entries into network input/label tuple.

        Args:
            sampled_entries (List[dict]): Entries from a DataFrame

        Returns:
            Tuple[NDArray, NDArray]: Network inputs and labels.
        """
        inputs, labels = [], []
        for sampled_entry in sampled_entries:
            x, y = self._process_entry(sampled_entry)
            if self.augmenter is not None:
                x = self._augment_input(x)
            x, y = self._normalize_input(x, y)
            inputs.append(x)
            labels.append(y)
        return np.array(inputs), np.array(labels)

    def _process_entry(self, sampled_entry: SampledEntry) -> Tuple[NDArray, NDArray]:
        """Extracts a tile from a slide at coordinates specified by the parsed entry.

        Args:
            sampled_entry (OpenslideExtractor.ParsedEntry): Parsed entry

        Returns:
            Tuple[NDArray, NDArray]: Input/label tuple
        """
        wsi = self._open_slide(str(Path(sampled_entry.metadata['slide_fp']).resolve()))
        x = self._extract_tile(wsi,
                                (sampled_entry.entry['coord_x'], sampled_entry.entry['coord_y']),
                                sampled_entry.metadata['tile_size'],
                                sampled_entry.metadata['sample_level']
                                )
        y = sampled_entry.entry['is_cancer']
        wsi.close()
        return x, y

    @staticmethod
    def _open_slide(slide_fp: Path) -> OpenSlide:
        """Opens slide of a given name in `slide_dir` directory.

        Args:
            slide_name (str): Name of a slide.

        Returns:
            OpenSlide: File handler to WSI
        """
        wsi = openslide.open_slide(slide_fp)
        return wsi

    def _extract_tile(
            self,
            wsi: OpenSlide,
            coords: Tuple[int, int],
            tile_size: int,
            level: int) -> np.ndarray:
        """Extracts a tile from a slide using the supplied coordinate values.

        Args:
            wsi (OpenSlide): File handler to WSI
            coords (Tuple[int, int]): (x,y) coordinates of a tile to be extracted
                at OpenSlide level 0 resolution.
            tile_size (int): Size of the tile to be extracted.
            level (int): Resolution level from which tile should be extracted.

        Returns:
            NDArray: RGB Tile represented as numpy array.
        """
        bg_tile = Image.new('RGB', (tile_size, tile_size), '#FFFFFF')
        im_tile = wsi.read_region(
            location=coords, level=level, size=(tile_size, tile_size)
        )
        bg_tile.paste(im_tile, None, im_tile)
        return np.array(bg_tile)

    @staticmethod
    def _normalize_input(x: NDArray, y: NDArray) -> Tuple[NDArray, NDArray]:
        """Normalizes images pixel values from [0-255] to [0-1] range.

        TODO: Maybe make this part of an augmenter?

        Args:
            x (NDArray): Network input
            y (NDArray): Label

        Returns:
            Tuple[NDArray]: Normalized input/label pair.
        """
        x = (x / 127.5) - 1
        return x, y

    def _augment_input(self, x: NDArray) -> Tuple[NDArray, NDArray]:
        """Applies augmentation on the input/label pair.

        Args:
            x (NDArray): Network input
            y (NDArray): Label

        Returns:
            Tuple[NDArray, NDArray]: Augmented input/label pair.
        """
        return self.augmenter(image=x)

    class Config(ConfigProto):
        def __init__(self, json_dict: dict):
            empty_configuration = dict(json_dict)
            empty_configuration.clear()
            super().__init__(empty_configuration)

        def parse(self):
            pass

class CytokeratinExtractor(OpenslideExtractor):
    def __init__(self, augmenter: Optional[ImgAugAugmenter], *args, **kwargs):
        self.augmenter = augmenter

    def __call__(self, sampled_entries: List[SampledEntry]) -> Tuple[np.ndarray, np.ndarray]:
        inputs, labels = [], []
        for sampled_entry in sampled_entries:
            x, y = self._process_entry(sampled_entry)
            if self.augmenter is not None:
                x, y = self._augment_input(x, y)
            x, y = self._normalize_input(x, y)
            inputs.append(x)
            labels.append(y)
        return np.array(inputs), np.array(labels)

    def _process_entry(self, sampled_entry: SampledEntry) -> Tuple[NDArray, NDArray]:
        he_wsi = self._open_slide(sampled_entry.metadata['slide_fp'])
        ce_wsi = self._open_slide(sampled_entry.metadata['annot_fp'])
        he = self._extract_tile(he_wsi,
            (sampled_entry.entry['coord_x'], sampled_entry.entry['coord_y']),
            sampled_entry.metadata['tile_size'],
            sampled_entry.metadata['sample_level']
            )
        he_wsi.close()

        ce = self._extract_tile(ce_wsi,
            (sampled_entry.entry['coord_x'], sampled_entry.entry['coord_y']),
            sampled_entry.metadata['tile_size'],
            sampled_entry.metadata['sample_level']
            )
        ce_wsi.close()

        # TODO: Slicing required by binary mask on the output. Make it configurable and move to extract_tile?
        return he, ce[:,:,:1]

    def _augment_input(self, x: NDArray, y: NDArray) -> Tuple[NDArray, NDArray]:
        y = self.augmenter.to_segmap(y)
        x,y = self.augmenter(image=x, segmentation_maps=y)
        return x, y.get_arr()

    @staticmethod
    def _normalize_input(x: NDArray, y: NDArray) -> Tuple[NDArray, NDArray]:
        """Normalizes images pixel values from [0-255] to [0-1] range.

        TODO: Maybe make this part of an augmenter?

        Args:
            x (NDArray): Network input
            y (NDArray): Label

        Returns:
            Tuple[NDArray]: Normalized input/label pair.
        """
        x = (x / 127.5) - 1
        y = (y / 255.0)
        return x, y

    class Config(ConfigProto):
        def __init__(self, json_dict: dict):
            empty_configuration = dict(json_dict)
            empty_configuration.clear()
            super().__init__(empty_configuration)

        def parse(self):
            pass

class GenericExtractor(Extractor):
    def __init__(self, config: ConfigProto, *args, **kwargs):
        self.config = config

    def __call__(self, sampled_entries: List[SampledEntry]) -> dict[str, np.ndarray]:
        return_dict = {}
        for return_key, return_list_def in self.config.return_definition.items():
            return_dict[return_key] = np.transpose([
                self.retrieve_return_value(sampled_entries, return_def)
                for return_def in return_list_def
            ])
        return return_dict

    def retrieve_return_value(self, sampled_entries, return_def):
        # Verify that exactly one cell type is specified
        xor_cell_type = ('entry' in return_def) \
            ^ ('metadata' in return_def) \
            ^ ('value' in return_def)
        assert xor_cell_type, 'Exactly one of ["entry", "metadata", "value"] must be defined.'

        # Convert type
        if 'dtype' in return_def:
            dtype = locate(return_def['dtype'])
        else:
            dtype = lambda x: x

        # Select cell type
        if 'entry' in return_def:
            cell_type = 'entry'
        elif 'metadata' in return_def:
            cell_type = 'metadata'
        else:
            cell_type = 'value'

        # Retrieve cell name
        column_name = return_def[cell_type]

        # Return result
        return np.array([
            dtype(asdict(sampled_entry)[cell_type][column_name])
            for sampled_entry in sampled_entries
        ])

    class Config(ConfigProto):
        def __init__(self, json_dict: dict):
            super().__init__(json_dict)
            self.return_definition = None

        def parse(self):
            self.return_definition = self.config['return']
