from collections import defaultdict
from typing import DefaultDict, List, Tuple

import bokeh.io
import bokeh.plotting
import numpy as np
from bokeh.layouts import column
from bokeh.models import CustomJS, HoverTool, Slider
from bokeh.plotting import ColumnDataSource, output_file, save

from l5kit.configs import load_config_data
from l5kit.data import ChunkedDataset, LocalDataManager
from l5kit.data.filter import (filter_agents_by_frames, filter_agents_by_labels, filter_tl_faces_by_frames,
                               filter_tl_faces_by_status)
from l5kit.data.labels import PERCEPTION_LABELS
from l5kit.data.map_api import MapAPI, TLFacesColors
from l5kit.geometry import compute_agent_pose, rotation33_as_yaw, transform_points
from l5kit.rasterization import build_rasterizer
from l5kit.rasterization.box_rasterizer import get_ego_as_agent
from l5kit.rasterization.semantic_rasterizer import indices_in_bounds
from l5kit.sampling import get_relative_poses


COLORS = {
    TLFacesColors.GREEN.name: "#33CC33",
    TLFacesColors.RED.name: "#FF3300",
    TLFacesColors.YELLOW.name: "#FFFF66",
    "PERCEPTION_LABEL_CAR": "#1F77B4",
    "PERCEPTION_LABEL_CYCLIST": "#CC33FF",
    "PERCEPTION_LABEL_PEDESTRIAN": "#66CCFF",
}


def get_frame_trajectories(frames: np.ndarray, agents_frames: List[np.ndarray], track_ids: np.ndarray,
                           frame_index: int) -> Tuple[DefaultDict, DefaultDict]:

    traj_agents = defaultdict(list)
    # TODO: factor out future length
    agent_traj_length = 20
    for track_id in track_ids:
        pos, *_, avail = get_relative_poses(agent_traj_length, frames[frame_index: frame_index + agent_traj_length],
                                            track_id, agents_frames[frame_index: frame_index + agent_traj_length],
                                            np.eye(3), 0)
        traj_agents["x"].append(pos[avail > 0, 0])
        traj_agents["y"].append(pos[avail > 0, 1])

    traj_ego = defaultdict(list)
    # TODO: factor out future length
    ego_traj_length = 100
    pos, *_, avail = get_relative_poses(ego_traj_length, frames[frame_index: frame_index + ego_traj_length],
                                        None, agents_frames[frame_index: frame_index + ego_traj_length],
                                        np.eye(3), 0)
    traj_ego["x"].append(pos[avail > 0, 0])
    traj_ego["y"].append(pos[avail > 0, 1])

    return traj_agents, traj_ego


def visualise_scene(zarr_dataset: ChunkedDataset, scene_index: int, mapAPI: MapAPI):
    output_file("scene.html")
    agent_hover = HoverTool(
        mode="mouse",
        names=["agent"],
        tooltips=[
            ("Type", "@name"),
            ("Probability", "@p{0.00}%"),
            ("Track id", "@id"),
        ],
    )

    f = bokeh.plotting.figure(
        title="Scene {}".format(scene_index),
        match_aspect=True,
        tools=["pan", "wheel_zoom", agent_hover, "save", "reset"],
        active_scroll="wheel_zoom",
    )

    f.xgrid.grid_line_color = None
    f.ygrid.grid_line_color = None

    # get a new dataset with only that scene
    scene_dataset = zarr_dataset.get_scene_dataset(scene_index)
    frames = scene_dataset.frames
    agents = scene_dataset.agents
    tl_faces = scene_dataset.tl_faces

    agents_frames = filter_agents_by_frames(frames, agents)
    tls_frames = filter_tl_faces_by_frames(frames, tl_faces)

    out = []
    for frame_idx in range(len(frames)):
        frame = frames[frame_idx]
        agents_frame = agents_frames[frame_idx]

        # TODO: hardcoded threshold
        agents_frame = filter_agents_by_labels(agents_frame, 0.1)

        tls_frame = tls_frames[frame_idx]

        lanes, crosswalks, ego, agent = get_frame_data(mapAPI, frame, agents_frame, tls_frame)
        trajs, traj_ego = get_frame_trajectories(frames, agents_frames, agents_frame["track_id"], frame_idx)

        trajs = ColumnDataSource(data=trajs)
        traj_ego = ColumnDataSource(data=traj_ego)

        lanes = ColumnDataSource(data=lanes)
        crosswalks = ColumnDataSource(data=crosswalks)
        ego = ColumnDataSource(data=ego)
        agent = ColumnDataSource(data=agent)

        out.append(dict(lanes=lanes, crosswalks=crosswalks, ego=ego, agent=agent, trajs=trajs, traj_ego=traj_ego))

    f.patches(xs="x", ys="y", color="color", line_width=0, alpha=0.5, source=out[0]["lanes"])
    f.patches("x", "y", line_width=0, alpha=0.5, color="#B5B50D", source=out[0]["crosswalks"])
    f.patches("x", "y", line_width=2, color="#B53331", source=out[0]["ego"])
    f.patches(xs="x", ys="y", color="color", line_width=2, name="agent", source=out[0]["agent"])
    f.multi_line("x", "y", alpha=0.8, color="pink", line_width=3, source=out[0]["trajs"], legend_label="trajs")
    f.multi_line("x", "y", alpha=0.8, color="red", line_width=3, source=out[0]["traj_ego"], legend_label="traj_ego")

    slider_callback = CustomJS(
        args=dict(sources=out[0], frames=out),
        code="""
            sources["lanes"].data = frames[cb_obj.value]["lanes"].data;
            sources["crosswalks"].data = frames[cb_obj.value]["crosswalks"].data;
            sources["agent"].data = frames[cb_obj.value]["agent"].data;
            sources["ego"].data = frames[cb_obj.value]["ego"].data;
            sources["trajs"].data = frames[cb_obj.value]["trajs"].data;
            sources["traj_ego"].data = frames[cb_obj.value]["traj_ego"].data;

            sources["lanes"].change.emit();
            sources["crosswalks"].change.emit();
            sources["agent"].change.emit();
            sources["ego"].change.emit();
            sources["trajs"].change.emit();
            sources["traj_ego"].change.emit();
        """,
    )

    slider = Slider(start=0, end=len(frames), value=0, step=1, title="frame")
    slider.js_on_change("value", slider_callback)

    f.legend.location = "top_left"
    f.legend.click_policy = "hide"

    layout = column(f, slider)
    save(layout)


def get_frame_data(mapAPI: MapAPI, frame: np.ndarray, agents: np.ndarray, tls_frame: np.ndarray):
    ego_xy = frame["ego_translation"][:2]

    #################
    # plot lanes
    lane_indices = indices_in_bounds(ego_xy, mapAPI.bounds_info["lanes"]["bounds"], 50)
    active_tl_ids = set(filter_tl_faces_by_status(tls_frame, "ACTIVE")["face_id"].tolist())

    lanes_dict = defaultdict(list)
    for idx, lane_idx in enumerate(lane_indices):
        lane_idx = mapAPI.bounds_info["lanes"]["ids"][lane_idx]

        lane_tl_ids = set(mapAPI.get_lane_traffic_control_ids(lane_idx))
        lane_colour = "gray"
        for tl_id in lane_tl_ids.intersection(active_tl_ids):
            lane_colour = COLORS[mapAPI.get_color_for_face(tl_id)]

        lane_coords = mapAPI.get_lane_coords(lane_idx)
        left_lane = lane_coords["xyz_left"][:, :2]
        right_lane = lane_coords["xyz_right"][::-1, :2]

        lanes_dict["x"].append(np.hstack((left_lane[:, 0], right_lane[:, 0])))
        lanes_dict["y"].append(np.hstack((left_lane[:, 1], right_lane[:, 1])))
        lanes_dict["color"].append(lane_colour)

    #################
    # plot crosswalks
    crosswalk_indices = indices_in_bounds(ego_xy, mapAPI.bounds_info["crosswalks"]["bounds"], 50)
    crosswalks_dict = dict(x=[], y=[])
    for idx in crosswalk_indices:
        crosswalk = mapAPI.get_crosswalk_coords(mapAPI.bounds_info["crosswalks"]["ids"][idx])
        crosswalks_dict["x"].append(crosswalk["xyz"][:, 0])
        crosswalks_dict["y"].append(crosswalk["xyz"][:, 1])

    #################
    # plot ego and agent cars

    ego_dict = dict(x=[], y=[])
    agents_dict = defaultdict(list)

    agents = np.insert(agents, 0, get_ego_as_agent(frame))

    # TODO: move to a function
    corners_base_coords = (np.asarray([[-1, -1], [-1, 1], [1, 1], [1, -1]]) * 0.5)[None, :, :]
    corners_m = corners_base_coords * agents["extent"][:, None, :2]  # corners in zero
    s = np.sin(agents["yaw"])
    c = np.cos(agents["yaw"])
    rotation_m = np.moveaxis(np.array(((c, s), (-s, c))), 2, 0)

    box_world_coords = corners_m @ rotation_m + agents["centroid"][:, None, :2]
    # ego
    ego_dict["x"] = [box_world_coords[0, :, 0]]
    ego_dict["y"] = [box_world_coords[0, :, 1]]

    agents = agents[1:]
    box_world_coords = box_world_coords[1:]

    label_indices = np.argmax(agents["label_probabilities"], axis=1)
    agents_dict["x"] = list(box_world_coords[..., 0])
    agents_dict["y"] = list(box_world_coords[..., 1])
    agents_dict["id"] = list(agents["track_id"])
    agents_dict["name"] = list(np.asarray(PERCEPTION_LABELS)[label_indices])
    agents_dict["p"] = list(agents["label_probabilities"][np.arange(len(agents)), label_indices])
    agents_dict["color"] = ["#1F77B4" if n not in COLORS else COLORS[n] for n in agents_dict["name"]]

    return lanes_dict, crosswalks_dict, ego_dict, agents_dict


if __name__ == "__main__":
    zarr_dt = ChunkedDataset("/tmp/l5kit_data/scenes/sample.zarr").open()
    print(zarr_dt)

    cfg = load_config_data(
        "/Users/lucabergamini/Desktop/l5kit/examples/agent_motion_prediction/agent_motion_config.yaml"
    )

    rast = build_rasterizer(cfg, LocalDataManager("/tmp/l5kit_data"))
    mapAPI = rast.sem_rast.mapAPI

    visualise_scene(zarr_dt, scene_index=0, mapAPI=mapAPI)