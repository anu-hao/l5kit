""" Utils for Closed Loop Evaluation """

from typing import DefaultDict, List

from prettytable import PrettyTable

from l5kit.cle.closed_loop_evaluator import ClosedLoopEvaluator, EvaluationPlan
from l5kit.cle.metrics import DisplacementErrorL2Metric, DistanceToRefTrajectoryMetric, SimulatedDrivenMilesMetric
from l5kit.cle.metrics import YawErrorCAMetric
from l5kit.cle.validators import ValidationCountingAggregator
from l5kit.simulation.dataset import SimulationDataset
from l5kit.simulation.unroll import SimulationOutput, UnrollInputOutput


def get_cle() -> ClosedLoopEvaluator:
    """ Get the Closed Loop Evaluator for L5 Gym environment
    :return: the closed loop evaluator
    """

    metrics = [DisplacementErrorL2Metric(),
               DistanceToRefTrajectoryMetric(scene_fraction=1.0),
               YawErrorCAMetric()]

    cle_evaluator = ClosedLoopEvaluator(EvaluationPlan(metrics=metrics,
                                        validators=[],
                                        composite_metrics=[],
                                        intervention_validators=[]))
    return cle_evaluator


def aggregate_cle_metrics(cle_evaluator: ClosedLoopEvaluator) -> None:
    validation_results_log = cle_evaluator.validation_results()
    agg_log = ValidationCountingAggregator().aggregate(validation_results_log)
    cle_evaluator.reset()

    fields = ["metric", "log_replayed agents"]
    table = PrettyTable(field_names=fields)
    for metric_name in agg_log:
        table.add_row([metric_name, agg_log[metric_name].item()])
    print(table)


class SimulationOutputGym(SimulationOutput):
    def __init__(self, scene_id: int, sim_dataset: SimulationDataset,
                 ego_ins_outs: DefaultDict[int, List[UnrollInputOutput]],
                 agents_ins_outs: DefaultDict[int, List[List[UnrollInputOutput]]]):
        """This object holds information about the result of the simulation loop
        for a given scene dataset in Gym

        :param scene_id: the scene indices
        :param sim_dataset: the simulation dataset
        :param ego_ins_outs: all inputs and outputs for ego (each frame of each scene has only one)
        :param agents_ins_outs: all inputs and outputs for agents (multiple per frame in a scene)
        """
        super(SimulationOutputGym, self).__init__(scene_id, sim_dataset, ego_ins_outs, agents_ins_outs)

        # Required for Bokeh Visualizer
        self.tls_frames = self.simulated_dataset.dataset.tl_faces
        self.agents_th = self.simulated_dataset.cfg["raster_params"]["filter_agents_threshold"]

        # Remove Dataset attributes
        del self.recorded_dataset
        del self.simulated_dataset