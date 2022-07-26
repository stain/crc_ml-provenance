"""Data augmenter definitions."""
# Standard Imports
from __future__ import annotations

import abc
from typing import NoReturn

# Third-party Imports
import imgaug.augmenters as iaa
from imgaug.augmentables.segmaps import SegmentationMapsOnImage
import numpy as np

# Local Imports
from rationai.utils.config import ConfigProto


class BaseAugmenter(abc.ABC):
    """Interface for data augmentation.

    If augmentation is turned on, an extractor calls its __call__ method after tile extraction.

    Attributes
    ----------
    config : rationai.datagens.BaseAugmenter.Config
        The configuration parser for augmenter.
    """
    def __init__(self, config: ConfigProto):
        self.config = config
        pass

    @abc.abstractmethod
    def __call__(self, *args, **kwargs):
        """Called by an extractor to perform data augmentation."""
        raise NotImplementedError('Override __call__ method to perform a data transformation')

    @staticmethod
    def to_segmap(img):
        return SegmentationMapsOnImage(img, img.shape)

    class Config(ConfigProto):
        """Each Augmenter should implement a nested Config class to process its configuration"""
        pass


class ImgAugAugmenter(BaseAugmenter):
    # noinspection PyUnresolvedReferences
    """Uses image augmenter: imgaug.augmenters.iaa

    This class should not be used directly, but subclassed.

    Attributes
    ----------
    config : rationai.datagens.BaseAugmenter.Config
        The configuration parser for augmenter.
    augmenter : imgaug.augmenters.meta.Augmenter
        The augmenter containing image transformation configuration, used when the __call__ method is used.
    """

    def __init__(self, config: ConfigProto):
        super().__init__(config)
        self.augmenter = iaa.Noop()

    def __call__(self, *args, **kwargs):
        """Augments input image(s).

        Return
        ------
        # TODO: Check the return type
        """
        return self.augmenter.augment(*args, **kwargs)


class ImageAugmenter(ImgAugAugmenter):
    # noinspection PyUnresolvedReferences
    """For information about what augmentations are used, see `ImageAugmenterConfig` class.

    Attributes
    ----------
    config : rationai.datagens.ImageAugmenter.Config
        Parsed ImageAugmenter configuration.
    augmenter : imgaug.augmenters.Sequential
        The augmenter containing image transformation configuration, used when the __call__ method is used.
    """

    def __init__(self, config: ConfigProto):
        super().__init__(config)

        # noinspection PyUnresolvedReferences
        self.augmenter = iaa.Sequential([
            iaa.Fliplr(
                self.config.horizontal_flip_proba,
                random_state=self.config.seed,
                name='horizontal'
            ),
            iaa.Flipud(
                self.config.vertical_flip_proba,
                random_state=self.config.seed,
                name='vertical'
            ),
            iaa.AddToBrightness(
                add=self.config.brightness_add_range,
                random_state=self.config.seed,
                name='brightness'
            ),
            iaa.AddToHueAndSaturation(
                value_saturation=self.config.saturation_add_range,
                value_hue=self.config.hue_add_range,
                name='hue_and_saturation',
                random_state=self.config.seed
            ),
            iaa.GammaContrast(
                gamma=self.config.contrast_scale_range,
                random_state=self.config.seed,
                name='contrast'
            ),
            iaa.geometric.Rot90(
                k=self.config.rotate_90_deg_interval,
                random_state=self.config.seed,
                name='rotate90'
            )
        ],
        random_state=self.config.seed)

    class Config(ConfigProto):
        # noinspection PyUnresolvedReferences
        """Configuration parser and wrapper for ImageAugmenter.

        Attributes
        ----------
        config : dict
            The dictionary containing the configuration to be parsed.
        horizontal_flip_proba : float
            Probability that an image will be flipped horizontally.
        vertical_flip_proba : float
            Probability that an image will be flipped vertically.
        brightness_add_range : tuple(int, int)
            Range from which to add to image brightness.
        saturation_add_range : tuple(int, int)
            Range from which to add to image saturation.
        hue_add_range : tuple(int, int)
            Range from which to add to image hue.
        contrast_scale_range : tuple(float, float)
            Range from which to choose scaling factor for contrast augmentation.
        rotate_90_deg_interval : tuple(int, int)
            Discrete interval from which to choose number of 90 degree rotations performed on an image.
        """

        # noinspection PyTypeChecker
        def __init__(self, json_dict: dict):
            super().__init__(json_dict)
            self.seed = None
            self.horizontal_flip_proba: float = None
            self.vertical_flip_proba: float = None
            self.brightness_add_range: tuple[int, int] = None
            self.saturation_add_range: tuple[int, int] = None
            self.hue_add_range: tuple[int, int] = None
            self.contrast_scale_range: tuple[float, float] = None
            self.rotate_90_deg_interval: tuple[int, int] = None

        def parse(self) -> NoReturn:
            """Parse SlideAugmenter configuration."""
            self.seed = self.config.get('seed', np.random.randint(low=0, high=999999))
            self.horizontal_flip_proba = float(self.config.get('horizontal_flip', 0.0))
            self.vertical_flip_proba = float(self.config.get('vertical_flip', 0.0))
            self.brightness_add_range = tuple(self.config.get('brightness_range', (0, 0)))
            self.saturation_add_range = tuple(self.config.get('saturation_range', (0, 0)))
            self.hue_add_range = tuple(self.config.get('hue_range', (0, 0)))
            self.contrast_scale_range = tuple(self.config.get('contrast_range', (1.0, 1.0)))
            self.rotate_90_deg_interval = tuple(self.config.get('rotate_interval', (0, 0)))


class NoOpImageAugmenter(ImgAugAugmenter):
    """This is the class to be used when no image augmentation operation is to be done."""

    def __init__(self, config: ConfigProto):
        super().__init__(config)

    class Config(ConfigProto):
        # noinspection PyUnresolvedReferences
        """Configuration parser and wrapper for NoOpImageAugmenter.

        Attributes
        ----------
        config : dict
            The dictionary containing the configuration to be parsed. Note that this is ignored, no matter what
            configuration is passed to the constructor.
        """

        def __init__(self, json_dict: dict):
            empty_configuration = dict(json_dict)
            empty_configuration.clear()
            super().__init__(empty_configuration)

        def parse(self) -> NoReturn:
            """Parse NoOpImageAugmenter configuration.

            No configuration is to be parsed.
            """
            pass
