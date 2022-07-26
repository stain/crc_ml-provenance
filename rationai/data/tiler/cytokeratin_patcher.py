# Standard Imports
from __future__ import annotations
from dataclasses import dataclass
from multiprocessing import Pool
from typing import Optional
from typing import Tuple
from typing import List
from typing import Iterator
from typing import Any
from pathlib import Path
from datetime import datetime
import argparse
import logging
import json
import copy

# Third-party Imports
import numpy as np
import warnings
import tables
from nptyping import NDArray
import pandas as pd
from pandas.core.frame import DataFrame
from PIL import Image
from PIL import ImageDraw
from skimage import color
from skimage import filters
from skimage import morphology
from openslide import OpenSlide

# Local Imports
from rationai.utils.utils import read_polygons
from rationai.utils.utils import open_pil_image
from rationai.utils.config import ConfigProto

# Allows to load large images
Image.MAX_IMAGE_PIXELS = None

# Suppress tables names warning
warnings.filterwarnings('ignore', category=tables.NaturalNameWarning)

log = logging.getLogger('slide-converter')
logging.basicConfig(level=logging.INFO,
                   format='[%(asctime)s][%(levelname).1s][%(process)d][%(filename)s][%(funcName)-25.25s] %(message)s',
                   datefmt='%d.%m.%Y %H:%M:%S')

@dataclass
class ROITile:
    coord_x: int
    coord_y: int

class SlideConverter:
    """Worker Object for tile extraction from WSI.

       Class (static) variable `dataset_h5` is a workaround for multiprocessing to work.
       HDFStore file handler does not support multiple write access. Thus a single file
       handler needs to be passed. This filehandler cannot be pickled and therefore cannot
       be stored as an instance variable or passed as an argument to the `__call__` function.
    """
    def __init__(self, config: ConfigProto):
        self.config = config

    def __call__(self, slide_fp: Path) -> Tuple[str, pd.DataFrame, dict]:
        """Converts slide into a coordinate map of ROI Tiles.

        Args:
            slide_fp (Path): Path to WSI file.

        Returns:
            Tuple[str, pd.DataFrame, dict]: Returns a tuple of table key,
                coordinate map dataframe and metadat dictionary.
        """
        self.slide_name = slide_fp.stem

        annot_fp = self._get_annotations()
        oslide_wsi = self._open_slide(slide_fp)

        is_wsi_levels_valid = self._validate_wsi_levels(oslide_wsi)

        if not is_wsi_levels_valid:
            return str(), pd.DataFrame(), dict()

        bg_mask_img = self._get_bg_mask(oslide_wsi)

        coord_map_df = self._tile_wsi_to_coord_map(oslide_wsi, bg_mask_img)
        table_key = self._get_table_key()
        metadata = self._get_table_metadata(slide_fp, annot_fp)

        oslide_wsi.close()
        return table_key, coord_map_df, metadata

    def _get_annotations(self) -> Optional[Path]:
        """Builds a path to annotation file using slide name and supplied annotation dir path.

        Returns:
            Optional[Path]: Path to annotation file if it exists; otherwise None.
        """
        annot_fp = (self.config.ce_dir / self.slide_name).with_suffix('.tif')
        if annot_fp.exists():
            log.debug(f'[{self.slide_name}] Annotation DAB found.')
            return annot_fp

        log.warning(f'[{self.slide_name}] Annotation DAB not found.')
        return None

    def _open_slide(self, slide_fp: Path) -> OpenSlide:
        """Opens WSI slide and returns handler.

        Args:
            slide_fp (Path): Path to WSI slide.

        Returns:
            OpenSlide: Handler to opened WSI slide.
        """
        logging.info(f'[{self.slide_name}] Opening slide: {str(slide_fp.resolve())}')
        return OpenSlide(str(slide_fp.resolve()))

    def _validate_wsi_levels(self, oslide_wsi: OpenSlide) -> bool:
        """Checks if WSI contains enough levels for slide successful slide conversion.

        Args:
            oslide_wsi (OpenSlide): Handler to WSI.

        Returns:
            bool: True if requirements are met; otherwise False.
        """
        max_level = max(self.config.sample_level, self.config.bg_level)
        if oslide_wsi.level_count < (max_level + 1):
            log.error(f'[{self.slide_name}] WSI does not contain {max_level + 1} levels.')
            return False
        return True

    def _get_bg_mask(self, oslide_wsi: OpenSlide) -> Image.Image:
        """Retrieves binary background mask.

        Mask is retrieved from disk if already present and force parameter is not set.
        Otherwise, the mask is drawn using image processing techniques on a WSI.

        Args:
            oslide_wsi (OpenSlide): Handler to WSI.
            annot_fp (Path): Path to annotation file.

        Returns:
            Image.Image: Binary background mask filtering background and highlighting tissue.
        """
        bg_mask_fp = self.config.output_path / f'masks/bg/bg_final/{self.slide_name}.PNG'
        if bg_mask_fp.exists() and not self.config.force:
            bg_mask_img = open_pil_image(bg_mask_fp)
            if bg_mask_img is not None:
                return bg_mask_img

        bg_mask_img = self._create_bg_mask(oslide_wsi)
        self._save_mask(bg_mask_img, bg_mask_fp)
        return bg_mask_img

    def _create_bg_mask(self, oslide_wsi: OpenSlide) -> Image.Image:
        """Creates binary background mask.

        Background mask is created by combining two masks:
             1) Mask obtained using image processing techniques applied to WSI
             2) Mask obtained using annotation file (if exists).

        Args:
            oslide_wsi (OpenSlide): Handler to WSI.
            annot_fp (Path): Path to annotation file.

        Returns:
            Image.Image: Binary background mask.
        """
        log.info(f'[{self.slide_name}] Generating new background mask.')
        init_bg_mask_img = self._get_init_bg_mask(oslide_wsi)

        return init_bg_mask_img

    def _get_init_bg_mask(self, oslide_wsi: OpenSlide) -> Image.Image:
        """Retrieves initial background mask created using image processing techniques.

        Mask is retrieved from disk if already present and force parameter is not set.
        Otherwise, the mask is drawn using image processing techniques.

        Args:
            oslide_wsi (OpenSlide): Handler to WSI.

        Returns:
            Image.Image: Binary background mask.
        """
        init_bg_mask_fp = self.config.output_path / f'masks/bg/bg_init/{self.slide_name}.PNG'
        if init_bg_mask_fp.exists() and not self.config.force:
            init_bg_mask_img = open_pil_image(init_bg_mask_fp)
            if init_bg_mask_img is not None:
                return init_bg_mask_img

        init_bg_mask_img = self._create_init_bg_mask(oslide_wsi)
        self._save_mask(init_bg_mask_img, init_bg_mask_fp)
        return init_bg_mask_img

    def _create_init_bg_mask(self, oslide_wsi: OpenSlide) -> Image.Image:
        """Draws binary background mask using image processing techniques.

        Args:
            oslide_wsi (OpenSlide): Handler to WSI.

        Returns:
            Image.Image: Binary background mask.
        """
        log.info(f'[{self.slide_name}] Generating new initial background mask.')
        wsi_img = oslide_wsi.read_region(
            location=(0, 0),
            level=self.config.bg_level,
            size=oslide_wsi.level_dimensions[self.config.bg_level]).convert('RGB')
        slide_hsv = color.rgb2hsv(np.array(wsi_img))
        saturation = slide_hsv[:, :, 1]
        threshold = filters.threshold_otsu(saturation)
        high_saturation = (saturation > threshold)
        disk_object = morphology.disk(self.config.disk_size)
        mask = morphology.closing(high_saturation, disk_object)
        mask = morphology.opening(mask, disk_object)
        return Image.fromarray(mask)

    def _prepare_empty_canvas(self, size: Tuple[int, int],
                               bg_color: str) -> Tuple[Image.Image, ImageDraw.ImageDraw]:
        """Prepares an empty canvas with default colour.

        Args:
            size (Tuple[int, int]): Size of a canvas.
            bg_color (str): Default colour of a canvas.

        Returns:
            Tuple[Image.Image, ImageDraw.ImageDraw]: Empty canvas and handler enabling drawing
                                                     on the canvas.
        """
        canvas = Image.new('L', size=size, color=bg_color)
        draw = ImageDraw.Draw(canvas)
        return canvas, draw

    def _draw_polygons_on_mask(self, polygons: List[List[float]],
                                canvas_draw: ImageDraw.ImageDraw,
                                polygon_color: str) -> None:
        """Draws polygons on a canvas based on provided annotation file.

        Args:
            polygons (List[List[float]]): List of polygons extracted from annotation file.
            canvas_draw (ImageDraw.ImageDraw): ImageDraw reference to canvas.
        """
        for polygon in polygons:
            if len(polygon) < 3:
                log.warning(f'[{self.slide_name}] Polygon {polygon} skipped because it contains less than 3 vertices.')
                continue
            canvas_draw.polygon(xy=polygon, outline=(polygon_color), fill=(polygon_color))

    def _combine_bg_masks(self, init_bg_mask_img: Image, annot_bg_mask_img: Image) -> Image.Image:
        """Combines two binary masks using binary AND operation.

        If slide conversion mode is set to 'Negative', initial background mask is returned.

        Args:
            init_bg_mask_img (Image): Binary background mask obtained using image processing
                                      techniques.
            annot_bg_mask_img (Image): Binary background mask obtained using supplied annotation
                                       file.

        Returns:
            Image.Image: Combined binary background mask.
        """
        combined_bg_mask = np.array(init_bg_mask_img)

        if not self.config.negative_mode:
            combined_bg_mask = combined_bg_mask & np.array(annot_bg_mask_img)

        return Image.fromarray(combined_bg_mask.astype(np.uint8) * 255, mode='L')

    def _save_mask(self, img: Image, output_fp: Path) -> None:
        """Saves binary mask image on disk.

        Args:
            img (Image): Binary mask.
            output_fp (Path): Output filepath.
        """
        img.save(str(output_fp), format='PNG')

    def _tile_wsi_to_coord_map(self, oslide_wsi: OpenSlide, bg_mask_img: Image) -> DataFrame:
        """Builds a coordinate map dataframe using extracted ROI tiles.

        Args:
            oslide_wsi (OpenSlide): Handler to WSI.
            bg_mask_img (Image): Binary background mask.

        Returns:
            DataFrame: Coordinate map of ROI tiles.
        """
        coord_map = {
            'coord_x': [],          # (int)  x-coordinate of a top-left pixel of the tile
            'coord_y': [],          # (int)  y-coordinate of a top-left pixel of the tile
            'slide_name': []        # (str)  slide identifier (filename)
        }
        log.info(f'[{self.slide_name}] Initiating slide conversion.')
        for roi_tile in self._roi_cutter(oslide_wsi, bg_mask_img):
            coord_map['coord_x'].append(roi_tile.coord_x)
            coord_map['coord_y'].append(roi_tile.coord_y)
            coord_map['slide_name'].append(self.slide_name)
        log.info(f'[{self.slide_name}] Slide conversion complete.')

        return pd.DataFrame.from_dict(coord_map)

    def _roi_cutter(self, oslide_wsi: OpenSlide, bg_mask_img: Image) -> Iterator[ROITile]:
        """Filters extracted tiles based on tissue coverage.

        Args:
            oslide_wsi (OpenSlide): Handler to WSI.
            bg_mask_img (Image): Binary background mask.
            annot_mask_img (Image): Binary annotation mask.

        Yields:
            Iterator[ROITile]: Iterator over extracted ROI tiles meeting all filtering requirements.
                               ROI Tile contains the following information:
                                    - coordinates of a tile (top-left pixel)
                                    - ratio of annotated pixels w.r.t. the whole tile
                                    - ratio of annotated pixels w.r.t. the center square of the tile
        """
        # Scale Factors
        bg_scale_factor = int(oslide_wsi.level_downsamples[self.config.bg_level])
        sampling_scale_factor = int(oslide_wsi.level_downsamples[self.config.sample_level])
        effective_scale_factor = bg_scale_factor // sampling_scale_factor

        # Dimensions
        wsi_width, wsi_height = oslide_wsi.level_dimensions[self.config.sample_level]

        for (coord_x, coord_y) in self._tile_cutter(wsi_height, wsi_width):
            bg_tile_img = self._crop_mask_to_tile(bg_mask_img, coord_x, coord_y, effective_scale_factor)

            if not self._is_bg_contain_tissue(bg_tile_img):
                continue

            yield ROITile(coord_x * sampling_scale_factor,
                  coord_y * sampling_scale_factor)

    def _tile_cutter(self, wsi_height: int, wsi_width: int) -> Iterator[Tuple[int, int]]:
        """Iterates over tile coordinates of a WSI.

        Args:
            wsi_height (int): WSI height.
            wsi_width (int): WSI width.

        Yields:
            Iterator[Tuple[int, int]]: Iterator over tile coordinates.
        """
        for coord_y in range(0, wsi_height, self.config.step_size):
            for coord_x in range(0, wsi_width, self.config.step_size):
                yield coord_x, coord_y

    def _crop_mask_to_tile(self, mask_img: Optional[Image.Image],
                            coord_x: int, coord_y: int,
                            scale_factor: int) -> Optional[Image.Image]:
        """Crops mask to a tile specified by coordinates scaled to appropriate resolution.

        Args:
            mask_img (Optional[Image.Image]): Image to be cropped.
            coord_x (int): Coordinate along x-axis.
            coord_y (int): Coordinate along y-axis.
            scale_factor (int): Used for scaling coordinates.

        Returns:
            Optional[Image.Image]: Cropped tiled or None
        """
        if mask_img is None:
            return None
        return mask_img.crop((int(coord_x // scale_factor),
                              int(coord_y // scale_factor),
                              int((coord_x + self.config.tile_size) // scale_factor),
                              int((coord_y + self.config.tile_size) // scale_factor)))

    def _is_bg_contain_tissue(self, tile_img: Image) -> bool:
        """Checks if tissue ratio in a tile falls within acceptable range.

        Args:
            tile_img (Image): Extracted tile, where non-zero element is considered a tissue.

        Returns:
            bool: True if tissue ratio falls within acceptable range; otherwise False.
        """
        tile_np = np.array(tile_img)
        tissue_coverage = self._calculate_tissue_coverage(tile_np, tile_np.size)
        return self.config.min_tissue <= tissue_coverage <= self.config.max_tissue

    def _calculate_tissue_coverage(self, tile_np: NDArray, size: int) -> float:
        """Calculates ratio of non-zero elements in a tile.

        Args:
            tile_np (NDArray): Extracted tile.
            size (int): length of a single side of a square area

        Returns:
            float: Ratio of non-zero elements.
        """
        tissue_count = np.count_nonzero(tile_np)
        return tissue_count / size

    def _get_table_key(self) -> str:
        return f'{self.config.group}/{self.slide_name}'

    def _get_table_metadata(self, slide_fp: Path, annot_fp: Path) -> dict:
        """Saves the following metadata with the table:
                - tile_size      size of the extracted tiles
                - center_size    size of the labelled center area
                - slide_fp       WSI filepath
                - annot_fp       XML Annotation filepath
                - sample_level   resolution level at which tiles were sampled

        Args:
            slide_fp (Path): HE WSI filepath
            annot_fp (Path): CE WSI filepath
        """
        metadata = dict()
        metadata['slide_fp'] = str(slide_fp)
        metadata['annot_fp'] = str(annot_fp)
        metadata['tile_size'] = self.config.tile_size
        metadata['sample_level'] = self.config.sample_level

        return metadata

    class Config(ConfigProto):
        """Iterable config for create map.

        The supplied config consists of two parts:

            - `_global` group is a mandatory key specifying default values. Parameters
            that are either same for every input or change only for some inputs
            should go in here.

            - One or more named groups. Each group custom group contains a list
            defining input files and parameters specific to these files. The
            value of these parameters will override the value of parameter
            defined in `_global` group.

        The config is an iterable object. At every iteration the SlideConverter.Config
        first defaults back to `_global` values before being overriden by the
        input specific parameters.
        """
        def __init__(self, config_fp):
            self.config_fp = config_fp

            # Input Path Parameters
            self.he_dir = None
            self.ce_dir = None
            self.pattern = None

            # Output Path Parameters
            self.output_path = None
            self.group = None

            # Tile Parameters
            self.tile_size = None
            self.step_size = None

            # Resolution Parameters
            self.sample_level = None
            self.bg_level = None

            # Filtering Parameters
            self.min_tissue = None
            self.max_tissue = None
            self.disk_size = None

            # Tiling Modes
            self.force = False

            # Paralellization Parameters
            self.max_workers = None

            # Holding changed values
            self._default_config = {}

            # Iterable State
            self._groups = None
            self._cur_group_configs = []

        def __iter__(self) -> SlideConverter.Config:
            """Populates the config parameters with default values.

            Returns:
                SlideConverter.Config: SlideConverter.Config with `_global` values.
            """
            log.info('Populating default options.')
            with open(self.config_fp, 'r') as json_r:
                config = json.load(json_r)['slide-converter']

            # Set config to default state
            self.__set_options(config.pop('_global'))
            self._default_config = {}

            # Prepare iterator variable
            self._groups = config
            return self

        def __next__(self) -> SlideConverter.Config:
            """First resets back default values before overriding the input specific
            parameters.

            Raises:
                StopIteration: No more input directories left to be processed

            Returns:
                SlideConverter.Config: Fully populated SlideConverter.Config ready to be processed.
            """
            if not (self._groups or self._cur_group_configs):
                raise StopIteration
            # For each input dir we only want to override
            # attributes explicitely configured in JSON file.
            self.__reset_to_default()
            if not self._cur_group_configs:
                self.__get_next_group()
            self.__set_options(self._cur_group_configs.pop())
            self.__validate_options()

            log.info(f'Now processing ({self.group}):{self.he_dir}')
            return self

        def __set_options(self, partial_config: dict) -> None:
            """Iterates over the variable names and values pairs a setting
            the corresponding instance variables to these values.

            Args:
                config (dict): Partial configuration specifying variables
                            and values to be overriden
            """
            for k,v in partial_config.items():
                self.__set_option(k, v)

        def __set_option(self, k: str, v: Any) -> None:
            """Sets instance variable `k` with values `v`.

            Args:
                k (str): name of the instance variable
                v (Any): value to be set
            """
            if hasattr(self, k):
                self._default_config[k] = getattr(self, k)
                setattr(self, k, v)
            else:
                log.warning(f'Attribute {k} does not exist.')

        def __reset_to_default(self) -> None:
            """Reverts the overriden values back to the default values.
            """
            # Reset to global state
            while self._default_config:
                k,v = self._default_config.popitem()
                setattr(self, k, v)

        def __get_next_group(self) -> None:
            """Retrieves the next named group from the JSON config.
            """
            group, configs = self._groups.popitem()
            self.group = group
            self._cur_group_configs = configs

        def __validate_options(self) -> None:
            """Converts string paths to Path objects.
            """
            # Path attributes
            self.he_dir = Path(self.he_dir)
            self.ce_dir = Path(self.ce_dir)
            self.output_path = Path(self.output_path)

def main(args):
    # Get file handler to the output dataset file
    dataset_h5 = pd.HDFStore((args.output_dir / args.output_dir.name).with_suffix('.h5'), 'w')

    # Spawn worker for each slide; maximum `max_workers` simultaneous workers.
    for cfg in SlideConverter.Config(args.config_fp):
        log.info(f'Spawning {cfg.max_workers} workers.')
        with Pool(cfg.max_workers) as p:
            for table_key, table, metadata in p.imap(SlideConverter(copy.deepcopy(cfg)), list(cfg.he_dir.glob(cfg.pattern))):
                if not table.empty:
                    dataset_h5.append(table_key, table)
                    dataset_h5.get_storer(table_key).attrs.metadata = metadata

    dataset_h5.close()

if __name__ == '__main__':

    description = """
    Slide conversion tool creates coordinate maps (table of coordinates) from the input slides.

    Example:
        python3 create_map.py path/to/config.json

             config_fp - Path to config file
    """

    parser = argparse.ArgumentParser(description=description,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)

    # Required arguments
    parser.add_argument('--config_fp', type=Path, required=True, help='Path to config file.')
    parser.add_argument('--output_dir', type=Path, required=True, help='Path to output directory.')
    args = parser.parse_args()
    main(args)
