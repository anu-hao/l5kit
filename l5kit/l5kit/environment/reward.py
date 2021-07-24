from abc import ABC, abstractmethod
from typing import DefaultDict, Dict, List, NamedTuple, Optional

import numpy as np
import torch

from l5kit.environment.cle_metricset import L5GymCLEMetricSet, L5MetricSet, SimulationOutputCLE
from l5kit.simulation.dataset import SimulationDataset
from l5kit.simulation.unroll import UnrollInputOutput


class RewardInput(NamedTuple):
    """The input tuple to calculate reward

    :param frame_index: the current step in the episode
    :param scene_indices: the list of scene indices rolled out in parallel
    :param sim_dataset: the input dataset corresponding to the scene_indices
    :param ego_ins_outs: object contain the ground truth and prediction information of the ego
    :param agents_ins_outs: object contain the ground truth and prediction information of the agents
    :param ego_output_dict: dictionary containing the predicted ego positions and yaws
    :param ego_input_dict: dictionary containing the target ego positions and yaws
    """
    frame_index: int
    scene_indices: List[int]
    sim_dataset: SimulationDataset
    ego_ins_outs: DefaultDict[int, List[UnrollInputOutput]]
    agents_ins_outs: DefaultDict[int, List[List[UnrollInputOutput]]]
    ego_output_dict: Dict[str, np.ndarray]
    ego_input_dict: Dict[str, np.ndarray]


class Reward(ABC):
    """Base class interface for gym environment reward."""
    #: The prefix that will identify this reward class
    reward_prefix: str

    @abstractmethod
    def reset(self) -> None:
        """Reset the reward state when new episode starts.
        """
        raise NotImplementedError

    @abstractmethod
    def get_reward(self, reward_input: RewardInput) -> float:
        """Return the reward at a particular time-step during the episode.
        """
        raise NotImplementedError


class CLE_Reward(Reward):
    """This class is responsible for calculating reward during close loop simulation
    within the gym-compatible L5Kit environment.

    :param reward_prefix: the prefix that will identify this reward class
    :param metric_set: the set of metrics to compute
    :param enable_clip: flag to determine whether to clip reward
    :param rew_clip_thresh: the threshold to clip the reward
    :param use_yaw: flag to penalize the yaw prediction
    :param yaw_weight: weight of the yaw error
    :param stop_flag: flag to early terminate episode if reward crosses a threshold
    :param stop_thresh: the reward threshold to early terminate an episode
    """

    def __init__(self, reward_prefix: str = "CLE", metric_set: Optional[L5MetricSet] = None,
                 enable_clip: bool = True, rew_clip_thresh: float = 15,
                 use_yaw: Optional[bool] = True, yaw_weight: Optional[float] = 3.0,
                 stop_flag: Optional[bool] = False, stop_thresh: Optional[float] = 20) -> None:
        """Constructor method
        """
        self.reward_prefix = reward_prefix
        # Metric Set
        self.metric_set = metric_set if metric_set is not None else L5GymCLEMetricSet()

        self.use_yaw = use_yaw
        self.yaw_weight = yaw_weight

        self.enable_clip = enable_clip
        self.rew_clip_thresh = rew_clip_thresh

        self.stop_flag = stop_flag
        self.stop_thresh = stop_thresh

    def reset(self) -> None:
        """Reset the closed loop evaluator when a new episode starts.
        """
        self.metric_set.reset()

    def get_reward(self, reward_input: RewardInput) -> float:
        """Get the reward for the given step in close loop training.

        :param reward_input: the input tuple for reward calculation
        :return: the reward is the combination of L2 error from groundtruth trajectory and (optionally) yaw error
        """
        frame_index = reward_input.frame_index
        scene_indices = reward_input.scene_indices
        sim_dataset = reward_input.sim_dataset
        ego_ins_outs = reward_input.ego_ins_outs
        agents_ins_outs = reward_input.agents_ins_outs

        assert len(scene_indices) == 1

        # generate simulated_outputs
        simulated_outputs: List[SimulationOutputCLE] = []
        for scene_idx in scene_indices:
            simulated_outputs.append(SimulationOutputCLE(scene_idx, sim_dataset, ego_ins_outs, agents_ins_outs))
        self.metric_set.evaluate(simulated_outputs)

        # get CLE metrics
        scene_metrics = self.metric_set.evaluator.scene_metric_results[scene_idx]
        dist_error = scene_metrics['displacement_error_l2'][frame_index + 1]
        yaw_error = self.yaw_weight * torch.abs(scene_metrics['yaw_error_ca'][frame_index + 1])

        # clip reward
        reward = float(-dist_error.item())
        if self.enable_clip:
            reward = max(-self.rew_clip_thresh, -dist_error.item())

        # use yaw
        if self.use_yaw:
            reward -= yaw_error.item()

        # for early stopping of episode
        self.stop_error = dist_error.item()

        return reward


class OLE_Reward(Reward):
    """This class is responsible for calculating reward during open loop simulation
    within the gym-compatible L5Kit environment.

    :param reward_prefix: the prefix that will identify this reward class
    """

    def __init__(self, reward_prefix: str = "CLE") -> None:
        """Constructor method
        """
        self.reward_prefix = reward_prefix

    def reset(self) -> None:
        """Reset the open loop evaluator when a new episode starts.
        """
        pass

    def get_reward(self, reward_input: RewardInput) -> float:
        """Get the reward for the given step in open loop training.

        :param reward_input: the input tuple for reward calculation
        :return: the reward is the L2 error from groundtruth trajectory
        """
        # Reward for open loop training (MSE)
        ego_output_dict = reward_input.ego_output_dict
        ego_input_dict = reward_input.ego_input_dict
        penalty = np.square(ego_output_dict["positions"] - ego_input_dict["target_positions"]).mean()
        reward = - float(penalty)

        return reward