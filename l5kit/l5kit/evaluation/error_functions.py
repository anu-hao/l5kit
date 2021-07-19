import math
from typing import Callable

import torch


# Error function type
ErrorFunction = Callable[[torch.Tensor, torch.Tensor], torch.Tensor]


def l2_error(pred: torch.Tensor, gt: torch.Tensor) -> torch.Tensor:
    """A function that takes pred, gt tensor and computes their L2 distance.

    :param pred: predicted tensor, size: [batch_size, num_dims]
    :param gt: gt tensor, size: [batch_size, num_dims]
    :return: l2 distance between the predicted and gt tensor, size: [batch_size,]
    """
    return torch.norm(pred - gt, p=2, dim=-1)


def closest_angle(angle_a: torch.Tensor, angle_b: torch.Tensor) -> torch.Tensor:
    """ Finds the closest angle between angle_b - angle_a in radians.
        Returns an angle between [-pi, pi]
    :param angle_a: a Tensor of angles in radians
    :param angle_b: a Tensor of angles in radians
    :return: The relative angle between A and B between [-pi, pi]
    """
    assert angle_a.shape == angle_b.shape
    two_pi = 2.0 * math.pi
    wrapped = torch.fmod(angle_b - angle_a, two_pi)
    return torch.fmod(2.0 * wrapped, two_pi) - wrapped