import glob
import h5py
import KDEpy as kp
import mpi4py.MPI as MPI
import numpy as np
import os
import pandas as pd
import pykonal
import scipy.sparse
import scipy.spatial
import shutil
import tempfile

from . import _dataio
from . import _clustering
from . import _constants
from . import _picklable
from . import _utilities

# Get logger handle.
logger = _utilities.get_logger(f"__main__.{__name__}")

# Define aliases.
TraveltimeInventory = pykonal.inventory.TraveltimeInventory
PointSourceSolver = pykonal.solver.PointSourceSolver
geo2sph = pykonal.transformations.geo2sph
sph2geo = pykonal.transformations.sph2geo
sph2xyz = pykonal.transformations.sph2xyz
xyz2sph = pykonal.transformations.xyz2sph

COMM       = MPI.COMM_WORLD
RANK       = COMM.Get_rank()
WORLD_SIZE = COMM.Get_size()
ROOT_RANK  = _constants.ROOT_RANK


class InversionIterator(object):
    """
    A class providing core functionality for iterating inversion
    procedure.
    """


    def __init__(self, argc):

        self._argc = argc
        self._arrivals = None
        self._cfg = None
        self._events = None
        self._iiter = 0
        self._ireal = 0
        self._phases = None
        self._projection_matrix = None
        self._pwave_model = None
        self._swave_model = None
        self._psr_model = None
        self._pwave_realization_stack = None
        self._swave_realization_stack = None
        self._psr_realization_stack = None
        self._pwave_variance = None
        self._swave_variance = None
        self._psr_variance = None
        self._residuals = None
        self._sensitivity_matrix = None
        self._stations = None
        self._step_size = None
        self._sampled_arrivals = None
        self._sampled_events = None
        self._voronoi_cells = None

        if RANK == ROOT_RANK:
            scratch_dir = argc.scratch_dir
            self._scratch_dir_obj = tempfile.TemporaryDirectory(dir=scratch_dir)
            self._scratch_dir = self._scratch_dir_obj.name

            _tempfile = tempfile.TemporaryFile(dir=argc.scratch_dir)
            self._f5_workspace = h5py.File(_tempfile, mode="w")

        self.synchronize(attrs=["scratch_dir"])


    def __del__(self):

        if RANK == ROOT_RANK:

            self._f5_workspace.close()
            shutil.rmtree(self.scratch_dir)


    def __enter__(self):

        return (self)


    def __exit__(self, exc_type, exc_value, exc_traceback):

        self.__del__()


    @property
    def argc(self):
        return (self._argc)

    @property
    def arrivals(self):
        return (self._arrivals)

    @arrivals.setter
    def arrivals(self, value):
        self._arrivals = value

    @property
    def cfg(self):
        return (self._cfg)

    @cfg.setter
    def cfg(self, value):
        self._cfg = value

    @property
    def events(self):
        return (self._events)

    @events.setter
    def events(self, value):
        value = value.sort_values("event_id")
        value = value.reset_index(drop=True)
        self._events = value

    @property
    def iiter(self):
        return (self._iiter)

    @iiter.setter
    def iiter(self, value):
        self._iiter = value

    @property
    def ireal(self):
        return (self._ireal)

    @ireal.setter
    def ireal(self, value):
        self._ireal = value

    @property
    def phases(self):
        return (self._phases)

    @phases.setter
    def phases(self, value):
        self._phases = value

    @property
    def projection_matrix(self):
        return (self._projection_matrix)

    @projection_matrix.setter
    def projection_matrix(self, value):
        self._projection_matrix = value

    @property
    def pwave_model(self) -> _picklable.ScalarField3D:
        return (self._pwave_model)

    @pwave_model.setter
    def pwave_model(self, value):
        self._pwave_model = value

    @property
    def pwave_realization_stack(self):
        if RANK == ROOT_RANK:
            if "pwave_stack" not in self._f5_workspace:
                self._f5_workspace.create_dataset(
                    "pwave_stack",
                    shape=(len(self.cfg["algorithm"]["hvrs"])*self.cfg["algorithm"]["nreal"], *self.pwave_model.npts),
                    dtype=_constants.DTYPE_REAL,
                    fillvalue=np.nan
                )

            return (self._f5_workspace["pwave_stack"])

        return (None)

    @property
    def pwave_variance(self) -> _picklable.ScalarField3D:
        field = _picklable.ScalarField3D(coord_sys="spherical")
        field.min_coords = self.pwave_model.min_coords
        field.node_intervals = self.pwave_model.node_intervals
        field.npts = self.pwave_model.npts
        stack = self._f5_workspace["pwave_stack"]
        stack = np.ma.masked_invalid(stack)
        var = np.var(stack, axis=0)
        field.values = var
        return (field)

    @property
    def raypath_dir(self):
        return (os.path.join(self.scratch_dir, "raypaths"))

    @property
    def residuals(self):
        return (self._residuals)

    @residuals.setter
    def residuals(self, value):
        self._residuals = value

    @property
    def sampled_arrivals(self):
        return (self._sampled_arrivals)

    @sampled_arrivals.setter
    def sampled_arrivals(self, value):
        self._sampled_arrivals = value

    @property
    def sampled_events(self):
        return (self._sampled_events)

    @sampled_events.setter
    def sampled_events(self, value):
        self._sampled_events = value

    @property
    def scratch_dir(self):
        return (self._scratch_dir)

    @scratch_dir.setter
    def scratch_dir(self, value):
        self._scratch_dir = value

    @property
    def sensitivity_matrix(self):
        return (self._sensitivity_matrix)

    @sensitivity_matrix.setter
    def sensitivity_matrix(self, value):
        self._sensitivity_matrix = value

    @property
    def stations(self):
        return (self._stations)

    @stations.setter
    def stations(self, value):
        self._stations = value

    @property
    def step_size(self):
        return (self._step_size)

    @step_size.setter
    def step_size(self, value):
        self._step_size = value

    @property
    def swave_model(self):
        return (self._swave_model)

    @swave_model.setter
    def swave_model(self, value):
        self._swave_model = value

    @property
    def swave_realization_stack(self):
        if RANK == ROOT_RANK:
            if "swave_stack" not in self._f5_workspace:
                self._f5_workspace.create_dataset(
                    "swave_stack",
                    shape=(len(self.cfg["algorithm"]["hvrs"])*self.cfg["algorithm"]["nreal"], *self.pwave_model.npts),
                    dtype=_constants.DTYPE_REAL,
                    fillvalue=np.nan
                )

            return (self._f5_workspace["swave_stack"])

        return (None)

    @property
    def swave_variance(self) -> _picklable.ScalarField3D:
        field = _picklable.ScalarField3D(coord_sys="spherical")
        field.min_coords = self.swave_model.min_coords
        field.node_intervals = self.swave_model.node_intervals
        field.npts = self.swave_model.npts
        stack = self._f5_workspace["swave_stack"]
        stack = np.ma.masked_invalid(stack)
        var = np.var(stack, axis=0)
        field.values = var
        return (field)
 
    @property
    def psr_model(self):
        return (self._psr_model)

    @psr_model.setter
    def psr_model(self, value):
        self._psr_model = value

    @property
    def psr_realization_stack(self):
        if RANK == ROOT_RANK:
            if "psr_stack" not in self._f5_workspace:
                self._f5_workspace.create_dataset(
                    "psr_stack",
                    shape=(len(self.cfg["algorithm"]["hvrs"])*self.cfg["algorithm"]["nreal"], *self.pwave_model.npts),
                    dtype=_constants.DTYPE_REAL,
                    fillvalue=np.nan
                )

            return (self._f5_workspace["psr_stack"])

        return (None)

    @property
    def psr_variance(self) -> _picklable.ScalarField3D:
        field = _picklable.ScalarField3D(coord_sys="spherical")
        field.min_coords = self.psr_model.min_coords
        field.node_intervals = self.psr_model.node_intervals
        field.npts = self.psr_model.npts
        stack = self._f5_workspace["psr_stack"]
        stack = np.ma.masked_invalid(stack)
        var = np.var(stack, axis=0)
        field.values = var
        return (field)
        
    @property
    def traveltime_dir(self):
        return (os.path.join(self.scratch_dir, "traveltimes"))

    @property
    def traveltime_inventory_path(self):
        return (os.path.join(self.scratch_dir, "traveltime_inventory.h5"))

    @property
    def voronoi_cells(self):
        return (self._voronoi_cells)

    @voronoi_cells.setter
    def voronoi_cells(self, value):
        self._voronoi_cells = value


    @_utilities.log_errors(logger)
    @_utilities.root_only(RANK)
    def _compute_model_update(self, phase):
        """
        Compute the model update for a single realization and appends
        the results to the realization stack.

        Only the root rank performs this operation.
        """

        logger.info(f"Computing {phase}-wave model update")

        if phase == "P":
            model = self.pwave_model
        elif phase == "S":
            model = self.swave_model
        else:
            raise (ValueError(f"Unrecognized phase ({phase}) supplied."))

        damp = self.cfg["algorithm"]["damp"]
        atol = self.cfg["algorithm"]["atol"]
        btol = self.cfg["algorithm"]["btol"]
        conlim = self.cfg["algorithm"]["conlim"]
        maxiter = self.cfg["algorithm"]["maxiter"]

        result = scipy.sparse.linalg.lsmr(
            self.sensitivity_matrix,
            self.residuals,
            damp,
            atol,
            btol,
            conlim,
            maxiter,
            show=False
        )
        x, istop, itn, normr, normar, norma, conda, normx = result

        logger.info(f"||G||         = {norma:8.1f}")
        logger.info(f"||Gm-d||      = {normr:8.1f}")
        logger.info(f"||m||         = {normx:8.1f}")
        logger.info(f"||G^-1||||G|| = {conda:8.1f}")

        nvoronoi = len(self.voronoi_cells)
        delta_slowness = self.projection_matrix * x[:nvoronoi]
        delta_slowness = delta_slowness.reshape(model.npts)
        slowness = np.power(model.values, -1) + delta_slowness
        velocity = np.power(slowness, -1)

        if phase == "P":
            self.pwave_realization_stack[self.ireal] = velocity
        else:
            self.swave_realization_stack[self.ireal] = velocity
            self.psr_realization_stack[self.ireal] = self.pwave_realization_stack[self.ireal]/self.swave_realization_stack[self.ireal]

        return (True)


    @_utilities.log_errors(logger)
    def _compute_sensitivity_matrix(self, phase, hvr):
        """
        Compute the sensitivity matrix.
        """

        logger.info(f"Computing {phase}-wave sensitivity matrix")

        raypath_dir = self.raypath_dir

        index_keys = ["network", "station"]
        arrivals = self.sampled_arrivals.set_index(index_keys)

        arrivals = arrivals.sort_index()
        arrivals = arrivals.astype({"event_id":str})

        if RANK == ROOT_RANK:

            nvoronoi = len(self.voronoi_cells)

            ids = arrivals.index.unique()
            self._dispatch(ids)

            logger.debug("Compiling sensitivity matrix.")
            column_idxs = COMM.gather(None, root=ROOT_RANK)
            nsegments = COMM.gather(None, root=ROOT_RANK)
            nonzero_values = COMM.gather(None, root=ROOT_RANK)
            residuals = COMM.gather(None, root=ROOT_RANK)

            column_idxs = list(filter(lambda x: x is not None, column_idxs))
            nsegments = list(filter(lambda x: x is not None, nsegments))
            nonzero_values = list(filter(lambda x: x is not None, nonzero_values))
            residuals = list(filter(lambda x: x is not None, residuals))


            column_idxs = np.concatenate(column_idxs)
            nonzero_values = np.concatenate(nonzero_values)
            residuals = np.concatenate(residuals)
            nsegments = np.concatenate(nsegments)

            row_idxs = [
                i for i in range(len(nsegments))
                  for j in range(nsegments[i])
            ]
            row_idxs = np.array(row_idxs)

            matrix = scipy.sparse.coo_matrix(
                (nonzero_values, (row_idxs, column_idxs)),
                shape=(len(nsegments), nvoronoi)
            )

            self.sensitivity_matrix = matrix
            self.residuals = residuals

        else:


            column_idxs = np.array([], dtype=_constants.DTYPE_INT)
            nsegments = np.array([], dtype=_constants.DTYPE_INT)
            nonzero_values = np.array([], dtype=_constants.DTYPE_REAL)
            residuals = np.array([], dtype=_constants.DTYPE_REAL)

            step_size = self.step_size
            events = self.events.astype({"event_id":str}).set_index("event_id")
            events["idx"] = range(len(events))

            while True:

                item = self._request_dispatch()

                if item is None:
                    logger.debug("Sentinel received. Gathering sensitivity matrix.")

                    column_idxs = COMM.gather(column_idxs, root=ROOT_RANK)
                    nsegments = COMM.gather(nsegments, root=ROOT_RANK)
                    nonzero_values = COMM.gather(nonzero_values, root=ROOT_RANK)
                    residuals = COMM.gather(residuals, root=ROOT_RANK)

                    break

                network, station = item

                # Get the subset of arrivals belonging to this station.
                _arrivals = arrivals.loc[[(network, station)]]
                _arrivals = _arrivals.set_index("event_id")

                # Open the raypath file.
                filename = f"{network}.{station}.{phase}.h5"
                path = os.path.join(raypath_dir, filename)
                raypath_file = h5py.File(path, mode="r")

                for event_id, arrival in _arrivals.iterrows():

                    event = events.loc[event_id]
                    idx = int(event["idx"])

                    raypath = raypath_file[phase][:, idx]
                    raypath = np.stack(raypath).T

                    _column_idxs, counts = self._projected_ray_idxs(raypath, hvr)
                    column_idxs = np.append(column_idxs, _column_idxs)
                    nsegments = np.append(nsegments, len(_column_idxs))
                    nonzero_values = np.append(nonzero_values, counts * step_size)
                    residuals = np.append(residuals, arrival["residual"])

                raypath_file.close()

        COMM.barrier()

        return (True)


    @_utilities.log_errors(logger)
    def _dispatch(self, ids, sentinel=None):
        """
        Dispatch ids to hungry workers, then dispatch sentinels.
        """

        logger.debug("Dispatching ids")

        for _id in ids:
            requesting_rank = COMM.recv(
                source=MPI.ANY_SOURCE,
                tag=_constants.DISPATCH_REQUEST_TAG
            )
            COMM.send(
                _id,
                dest=requesting_rank,
                tag=_constants.DISPATCH_TRANSMISSION_TAG
            )
        # Distribute sentinel.
        for irank in range(WORLD_SIZE - 1):
            requesting_rank = COMM.recv(
                source=MPI.ANY_SOURCE,
                tag=_constants.DISPATCH_REQUEST_TAG
            )
            COMM.send(
                sentinel,
                dest=requesting_rank,
                tag=_constants.DISPATCH_TRANSMISSION_TAG
            )

        return (True)


    @_utilities.log_errors(logger)
    def _generate_voronoi_cells(self, phase, kvoronoi, nvoronoi, alpha):
        """
        Generate Voronoi cells using k-medians clustering of raypaths.
        """

        logger.debug(
            f"Generating {kvoronoi} Voronoi cells using k-medians clustering "
            f"and {nvoronoi} randomly distributed base Voronoi cells."
        )

        if RANK == ROOT_RANK:

            min_coords = self.pwave_model.min_coords
            max_coords = self.pwave_model.max_coords

            delta = max_coords - min_coords
            
            if alpha == 0:
                rho = np.random.rand(nvoronoi, 1) * delta[0] + min_coords[0]
                
            else:
                rho_base = np.random.rand(nvoronoi-kvoronoi, 1) * delta[0] + min_coords[0]
                #rho_refine = max_coords[0] - np.random.pareto(alpha, size=(kvoronoi, 1)) * delta[0]
                randpts = np.random.gamma(2.0, alpha, size = (kvoronoi,1))
                randpts = randpts / randpts.max()
                rho_refine = max_coords[0] - randpts * delta[0]
                # rho = np.vstack([rho_base,rho_refine[::-1]])
                rho = np.vstack([rho_base,rho_refine])
                
            theta_phi = np.random.rand(nvoronoi, 2) * delta[1:]  +  min_coords[1:]

            base_cells = np.hstack([rho, theta_phi])
            
            # base_cells = np.random.rand(nvoronoi, 3) * delta  +  min_coords

            self.voronoi_cells = base_cells

            if kvoronoi > 0:

                k_medians_npts = self.cfg["algorithm"]["k_medians_npts"]

                raypaths = []
                raypath_dir = self.raypath_dir

                columns = ["network", "station"]
                arrivals = self.sampled_arrivals.set_index(columns)
                arrivals = arrivals.sort_index()
                index = arrivals.index.unique()

                events = self.events.set_index("event_id")
                events["idx"] = np.arange(len(events))

                points = np.empty((0, 3))

                for network, station in index:

                    # Open the raypath file.
                    filename = f"{network}.{station}.{phase}.h5"
                    path = os.path.join(raypath_dir, filename)
                    raypath_file = h5py.File(path, mode="r")

                    event_ids = arrivals.loc[[(network, station)], "event_id"]
                    idxs = events.loc[event_ids, "idx"]
                    idxs = np.sort(idxs)

                    _points = raypath_file[phase][:, idxs]
                    if _points.ndim > 1:
                        _points = np.apply_along_axis(np.concatenate, 1, _points)
                    else:
                        _points = np.stack(_points)
                    _points = _points.T

                    points = np.vstack([points, _points])

                idxs = np.arange(len(points))
                idxs = np.random.choice(idxs, k_medians_npts, replace=False)
                points = points[idxs]

                medians = _clustering.k_medians(kvoronoi, points)

                self.voronoi_cells = np.vstack([self.voronoi_cells, medians])

        self.synchronize(attrs=["voronoi_cells"])

        return (True)


    @_utilities.log_errors(logger)
    def _projected_ray_idxs(self, raypath, hvr):
        """
        Return the cell IDs (column IDs) of each segment of the given
        raypath and the length of each segment in counts.
        """

        # hvr = self.cfg["algorithm"]["hvr"]
        min_coords = self.pwave_model.min_coords
        max_coords = self.pwave_model.max_coords
        center = (min_coords + max_coords) / 2

        voronoi_cells = self.voronoi_cells
        voronoi_cells = center + (voronoi_cells - center) / [1, hvr, hvr]

        voronoi_cells = sph2xyz(voronoi_cells)
        tree = scipy.spatial.cKDTree(voronoi_cells)

        raypath = center + (raypath - center) / [1, hvr, hvr]
        raypath = sph2xyz(raypath)
        _, column_idxs = tree.query(raypath)
        column_idxs, counts = np.unique(column_idxs, return_counts=True)

        return (column_idxs, counts)


    @_utilities.log_errors(logger)
    def _request_dispatch(self):
        """
        Request, receive, and return item from dispatcher.
        """
        COMM.send(
            RANK,
            dest=ROOT_RANK,
            tag=_constants.DISPATCH_REQUEST_TAG
        )
        item = COMM.recv(
            source=ROOT_RANK,
            tag=_constants.DISPATCH_TRANSMISSION_TAG
        )

        return (item)


    @_utilities.log_errors(logger)
    @_utilities.root_only(RANK)
    def _reset_realization_stack(self, phase):
        """
        Reset the realization stack values to np.nan for the given phase.

        Return True upon successful completion.
        """

        phase = phase.lower()
        handle = f"{phase}wave_realization_stack"
        stack = getattr(self, handle)
        stack[:] = np.nan

        if phase=="s":
            handle = f"psr_realization_stack"
            stack = getattr(self, handle)
            stack[:] = np.nan
            
        return (True)


    @_utilities.log_errors(logger)
    def _sample_arrivals(self, phase):
        """
        Draw a random sample of arrivals and update the
        "sampled_arrivals" attribute.
        """

        if RANK == ROOT_RANK:
            tukey_k = self.cfg["algorithm"]["outlier_removal_factor"]
            max_arr_resid = self.cfg["algorithm"]["max_arrival_residual"] #NEW
            narrival = self.cfg["algorithm"]["narrival"]

            # Subset for the arrivals associated with sampled events.
            arrivals = self.arrivals.set_index("event_id")
            arrivals = arrivals.sort_index()
            event_ids = self.sampled_events["event_id"]
            arrivals = arrivals.loc[event_ids]
            arrivals = arrivals.reset_index()

            # Subset for the appropriate phase.
            arrivals = arrivals.set_index("phase")
            arrivals = arrivals.sort_index()
            arrivals = arrivals.loc[phase]

            # Remove outliers.
            # arrivals = remove_outliers(arrivals, tukey_k, "residual")
            arrivals = remove_outliers(arrivals, tukey_k, "residual", max_arr_resid)
            min_arr = min([len(arrivals),narrival])
            arrivals = arrivals.sample(n=min_arr, weights="weight", replace=False)
            # Sample arrivals.
            # replace = True if narrival > len(arrivals) else False
            # arrivals = arrivals.sample(n=narrival, weights="weight", replace=replace)

            self.sampled_arrivals = arrivals

        self.synchronize(attrs=["sampled_arrivals"])

        return (True)


    @_utilities.log_errors(logger)
    def _sample_events(self, nevent):
        """
        Draw a random sample of events and update the
        "sampled_events" attribute.
        """

        if RANK == ROOT_RANK:

            # Sample events.
            events = self.events
            max_evt_resid = self.cfg["algorithm"]["max_event_residual"] #NEW

            events = remove_outliers(events, None, "residual", max_evt_resid) #NEW
            
            nevent = min([len(events),nevent])
            events = events.sample(n=nevent, weights="weight")

            self.sampled_events = events
            sampled_indices = self.sampled_events.index
            
            self.events.loc[sampled_indices,"sampling_count"]+=1

        self.synchronize(attrs=["sampled_events", "events"])

        return (True)


    @_utilities.log_errors(logger)
    def _trace_rays(self, phase):
        """
        Trace rays for all arrivals in self.sampled_arrivals and store
        in HDF5 file. Only trace non-existent raypaths.
        """

        logger.info("Tracing rays.")

        raypath_dir = self.raypath_dir
        arrivals = self.sampled_arrivals
        arrivals = arrivals.set_index(["network", "station"])
        arrivals = arrivals.sort_index()
        arrivals = arrivals.astype({'event_id':str})

        if RANK == ROOT_RANK:

            os.makedirs(raypath_dir, exist_ok=True)
            index = arrivals.index.unique()
            self._dispatch(index)

        else:

            events = self.events.astype({'event_id':str})
            events = events.set_index("event_id")
            events["idx"] = range(len(events))

            _path = self.traveltime_inventory_path
            with TraveltimeInventory(_path, mode="r") as traveltime_inventory:

                while True:

                    item = self._request_dispatch()

                    if item is None:
                        logger.debug("Sentinel received.")
                        break

                    network, station = item
                    handle = "/".join([network, station, phase])

                    traveltime = traveltime_inventory.read(handle)

                    filename = ".".join([network, station, phase])
                    path = os.path.join(raypath_dir, filename + ".h5")
                    raypath_file = h5py.File(path, mode="a")

                    if phase not in raypath_file:
                        dtype = h5py.vlen_dtype(_constants.DTYPE_REAL)
                        dataset = raypath_file.create_dataset(
                            phase,
                            (3, len(events),),
                            dtype=dtype
                        )
                    else:
                        dataset = raypath_file[phase]

                    event_ids = arrivals.loc[[(network, station)], "event_id"].values

                    for event_id in event_ids:

                        event = events.loc[event_id]
                        idx = int(event["idx"])

                        if np.stack(dataset[:, idx]).size != 0:
                            continue

                        columns = ["latitude", "longitude", "depth"]
                        coords = event[columns]
                        coords = geo2sph(coords)
                        raypath = traveltime.trace_ray(coords)
                        dataset[:, idx] = raypath.T.copy()

                    raypath_file.close()

        COMM.barrier()

        return (True)


    @_utilities.log_errors(logger)
    def _update_arrival_weights(
        self,
        phase: str,
        npts: int=16,
        bandwidth: int=0.1
    ) -> bool:
        """
        Update arrival weights using KDE.
        """

        logger.info("Updating weights for homogeneous raypath sampling.")

        if RANK == ROOT_RANK:
            arrivals = self.arrivals
            arrivals = arrivals[arrivals["phase"] == phase]

            # Merge event data.
            events = self.events.rename(
                columns={
                    "latitude": "event_latitude",
                    "longitude": "event_longitude",
                    "depth": "event_depth"
                }
            )

            merge_columns = [
                "event_latitude",
                "event_longitude",
                "event_depth",
                "event_id"
            ]

            arrivals = arrivals.merge(events[merge_columns], on="event_id")

            # Merge station data.
            stations = self.stations.rename(
                columns={
                    "latitude": "station_latitude",
                    "longitude": "station_longitude"
                }
            )

            merge_columns = [
                "station_latitude",
                "station_longitude",
                "network",
                "station"
            ]
            merge_keys = ["network", "station"]
            arrivals = arrivals.merge(stations[merge_columns], on=merge_keys)

            # Compute station-to-event azimuth and epicentral distance.
            dlat = arrivals["event_latitude"] - arrivals["station_latitude"]
            dlon = arrivals["event_longitude"] - arrivals["station_longitude"]
            arrivals["azimuth"] = np.arctan2(dlat, dlon)
            arrivals["delta"] = np.sqrt(dlat ** 2  +  dlon ** 2)

            # Extract the data for KDE fitting.
            kde_columns = [
                "event_latitude",
                "event_longitude",
                "event_depth",
                "azimuth",
                "delta"
            ]
            ndim = len(kde_columns)
            data = arrivals[kde_columns].values

            # Normalize the data.
            data_min = data.min(axis=0)
            data_max = data.max(axis=0)
            data_range = data_max - data_min
            data_delta = data - data_min
            data = data_delta / data_range

            # Fit and evaluate the KDE.
            kde = kp.FFTKDE(bw=bandwidth).fit(data)
            points, values = kde.evaluate(npts)
            points = [np.unique(points[:,iax]) for iax in range(ndim)]
            values = values.reshape((npts,) * ndim)

            # Initialize an interpolator because FFTKDE is evaluated on a
            # regular grid.
            interpolator = scipy.interpolate.RegularGridInterpolator(points, values)

            # Assign weights to the arrivals.
            arrivals["weight"] = 1 / np.exp(interpolator(data))

            # Update the self.arrivals attribute with weights.
            index_columns = ["network", "station", "event_id", "phase"]
            arrivals = arrivals.set_index(index_columns)
            _arrivals = self.arrivals.set_index(index_columns)
            _arrivals = _arrivals.sort_index()
            idx = arrivals.index
            _arrivals.loc[idx, "weight"] = arrivals["weight"]
            _arrivals = _arrivals.reset_index()
            self.arrivals = _arrivals

        self.synchronize(attrs=["arrivals"])

        return (True)

    @_utilities.log_errors(logger)
    def _update_events_weights(
        self,
        npts: int=16,
        bandwidth: float=0.1
    ) -> bool:
        """
        Update events weights using KDE.
        """

        logger.info("Updating event weights for homogeneous raypath sampling.")

        if RANK == ROOT_RANK:

            # Merge event data.
            events = self.events#.astype({"event_id":str})
            
            cols = list(set(list(events.columns) + ["weight"]))
            
            niter = self.cfg["algorithm"]["niter"]
            weight_scheme = self.cfg["algorithm"]["weight_scheme"]
            earthquake_coverage = self.cfg["algorithm"]["earthquake_coverage"]
            arrivals = self.arrivals#.astype({"event_id":str})
            stations = self.stations.rename(
                columns={
                    "latitude": "station_latitude",
                    "longitude": "station_longitude"
                }
            )
            
            
            merge_event_columns = [
                "event_id",
                "latitude",
                "longitude"
            ]
            arrivals = arrivals.merge(events[merge_event_columns],on="event_id")
            
            
            merge_station_columns = [
                "network",
                "station",
                "station_latitude",
                "station_longitude"
            ]
            arrivals = arrivals.merge(stations[merge_station_columns],on=["station","network"])
            dlat = arrivals["latitude"] - arrivals["station_latitude"]
            dlon = arrivals["longitude"] - arrivals["station_longitude"]
            arrivals["azimuth"] = np.degrees(np.arctan2(dlon,dlat))%360

            
            result = arrivals.groupby('event_id').apply(get_gap).reset_index(name='gap')
            if 'gap' in events.columns.values:
                events.drop(columns='gap',inplace=True)
            events = events.merge(result,on="event_id")
            events['coverage_normalized'] = (360-events['gap'])/360

            # Extract the data for KDE fitting.
            kde_columns = [
                "latitude",
                "longitude",
                "depth"
            ]
            ndim = len(kde_columns)
            data = events[kde_columns].values

            # Normalize the data.
            data_min = data.min(axis=0)
            data_max = data.max(axis=0)
            data_range = data_max - data_min
            data_range[data_range == 0] = 1e-5 #failsafe
            data_delta = data - data_min
            data = data_delta / data_range

            # Fit and evaluate the KDE.
            kde = kp.FFTKDE(bw=bandwidth).fit(data)
            points, values = kde.evaluate(npts)
            points = [np.unique(points[:,iax]) for iax in range(ndim)]
            values = values.reshape((npts,) * ndim)

            # Initialize an interpolator because FFTKDE is evaluated on a
            # regular grid.
            interpolator = scipy.interpolate.RegularGridInterpolator(points, values)

            # Assign weights to the arrivals.
            if self.iiter in weight_scheme[0]:
                events["weight"] = 1.0 / np.exp(interpolator(data))
            elif self.iiter in weight_scheme[1]:
                events["weight"] = 1.0 / interpolator(data)
            elif self.iiter in weight_scheme[2]:
                events["weight"] = 1.0 / np.log(1+interpolator(data))
            elif self.iiter in weight_scheme[3]:
                events["weight"] = 1.0
            
            if self.iiter in earthquake_coverage[1:]:
                events.loc[events.coverage_normalized<earthquake_coverage[0],"weight"] = events.weight.min()
                
           if self.iiter==1:
               events['sampling_count'] = 0
            
            events = events[cols]
            
            self.events = events

        self.synchronize(attrs=["events"])

        return (True)


    @_utilities.log_errors(logger)
    def _update_projection_matrix(self, hvr):
        """
        Update the projection matrix using the current Voronoi cells.
        """

        logger.info("Updating projection matrix")

        if RANK == ROOT_RANK:

            nvoronoi = len(self.voronoi_cells)
            # hvr = self.cfg["algorithm"]["hvr"]
            min_coords = self.pwave_model.min_coords
            max_coords = self.pwave_model.max_coords
            center = (min_coords + max_coords) / 2

            voronoi_cells = self.voronoi_cells
            voronoi_cells = center + (voronoi_cells - center) / [1, hvr, hvr]

            voronoi_cells = sph2xyz(voronoi_cells)
            tree = scipy.spatial.cKDTree(voronoi_cells)

            nodes = self.pwave_model.nodes
            nodes = center + (nodes - center) / [1, hvr, hvr]
            nodes = nodes.reshape(-1, 3)
            nodes = sph2xyz(nodes)

            _, column_ids = tree.query(nodes)

            nnodes = np.prod(self.pwave_model.nodes.shape[:-1])
            row_ids = np.arange(nnodes)

            values = np.ones(nnodes,)
            self.projection_matrix = scipy.sparse.coo_matrix(
                (values, (row_ids, column_ids)),
                shape=(nnodes, nvoronoi)
            )

        self.synchronize(attrs=["projection_matrix"])

        return (True)


    @_utilities.log_errors(logger)
    def compute_traveltime_lookup_tables(self):
        """
        Compute traveltime-lookup tables.
        """

        logger.info("Computing traveltime-lookup tables.")

        traveltime_dir = self.traveltime_dir

        logger.debug(f"Working in {traveltime_dir}")

        if RANK == ROOT_RANK:

            os.makedirs(traveltime_dir, exist_ok=True)
            ids = zip(self.stations["network"], self.stations["station"])
            self._dispatch(sorted(ids))

        else:

            geometry = self.stations
            geometry = geometry.set_index(["network", "station"])

            while True:

                # Request an event
                item = self._request_dispatch()

                if item is None:
                    logger.debug("Received sentinel.")

                    break

                network, station = item

                keys = ["latitude", "longitude", "depth"]
                coords = geometry.loc[(network, station), keys]
                coords = geo2sph(coords)

                for phase in self.phases:
                    handle = f"{phase.lower()}wave_model"
                    model = getattr(self, handle)
                    solver = PointSourceSolver(coord_sys="spherical")
                    solver.vv.min_coords = model.min_coords
                    solver.vv.node_intervals = model.node_intervals
                    solver.vv.npts = model.npts
                    solver.vv.values = model.values
                    solver.src_loc = coords
                    solver.solve()
                    path = os.path.join(
                        traveltime_dir,
                        f"{network}.{station}.{phase}.h5"
                    )
                    solver.tt.to_hdf(path)

        COMM.barrier()

        if RANK == ROOT_RANK:

            _path = self.traveltime_inventory_path
            with TraveltimeInventory(_path, mode="w") as tt_inventory:

                pattern = os.path.join(traveltime_dir, "*.h5")
                paths = glob.glob(pattern)
                paths = sorted(paths)
                tt_inventory.merge(paths)

            shutil.rmtree(self.traveltime_dir)

        COMM.barrier()

        return (True)


    @_utilities.log_errors(logger)
    def iterate(self):
        """
        Execute one iteration the entire inversion procedure including
        updating velocity models, event locations, and arrival residuals.
        """

        output_dir = self.argc.output_dir

        niter = self.cfg["algorithm"]["niter"]
        kvoronois = self.cfg["algorithm"]["kvoronois"]
        nvoronois = self.cfg["algorithm"]["nvoronois"]
        hvrs = self.cfg["algorithm"]["hvrs"]
        nreal = self.cfg["algorithm"]["nreal"]
        relocation_method = self.cfg["relocate"]["method"]
        alpha = self.cfg["algorithm"]["paretos_alpha"]
        add_variability = self.cfg["algorithm"]["add_variability"]
        nevent = self.cfg["algorithm"]["nevent"]
        
        
        kvoronoi = kvoronois[self.iiter]
        nvoronoi = nvoronois[self.iiter]

        self.iiter += 1

        
        logger.info(f"Iteration #{self.iiter} (/{niter}).")
        self._update_events_weights()
        for phase in self.phases:
            logger.info(f"Updating {phase}-wave model")
            self._reset_realization_stack(phase)
            self._update_arrival_weights(phase)
            self.ireal = 0
            for hvr in hvrs:
                for _ in range(nreal):
                    logger.info(f"Realization #{self.ireal+1} (/{nreal}).")

                    var1 = np.random.uniform(0.9, 1.1) if add_variability else 1
                    var2 = np.random.uniform(0.9, 1.1) if add_variability else 1
                    var3 = np.random.uniform(0.9, 1.1) if add_variability else 1
                    alp = np.random.randint(0, alpha+1) if add_variability else alpha
                    kvor = int(kvoronoi*var1)
                    nvor = int(nvoronoi*var2)
                    nev = int(nevent*var3)
                    
                    self._sample_events(nev)
                    self._sample_arrivals(phase)
                    self._trace_rays(phase)
                    self._generate_voronoi_cells(
                        phase,
                        kvor,
                        nvor,
                        alp
                    )
                    self._update_projection_matrix(hvr)
                    self._compute_sensitivity_matrix(phase, hvr)
                    self._compute_model_update(phase)
                    self.ireal += 1
                    
            self.update_model(phase)
            self.save_model(phase)
        self.compute_traveltime_lookup_tables()
        self.relocate_events(method=relocation_method)
        self.purge_raypaths()
        self.update_arrival_residuals()
        self.save_events()


    @_utilities.log_errors(logger)
    def load_cfg(self):
        """
        Parse and store configuration-file parameters.

        ROOT_RANK parses configuration file and broadcasts contents to all
        other processes.
        """

        logger.info("Loading configuration-file parameters.")

        if RANK == ROOT_RANK:

            # Parse configuration-file parameters.
            self.cfg = _utilities.parse_cfg(self.argc.configuration_file)
            _utilities.write_cfg(self.argc, self.cfg)

        self.synchronize(attrs=["cfg"])

        return (True)


    @_utilities.log_errors(logger)
    def load_event_data(self):
        """
        Parse and return event data from file.

        ROOT_RANK parses file and broadcasts contents to all other
        processes.
        """

        logger.info("Loading event data.")

        if RANK == ROOT_RANK:

            # Parse event data.
            data = _dataio.parse_event_data(self.argc)
            self.events, self.arrivals = data

            # Register the available phase types.
            phases = self.arrivals["phase"]
            phases = phases.unique()
            self.phases = sorted(phases)

        self.synchronize(attrs=["events", "arrivals", "phases"])

        return (True)


    @_utilities.log_errors(logger)
    def load_network_geometry(self):
        """
        Parse and return network geometry from file.

        ROOT_RANK parses file and broadcasts contents to all other
        processes.
        """

        logger.info("Loading network geometry")

        if RANK == ROOT_RANK:

            # Parse event data.
            stations = _dataio.parse_network_geometry(self.argc)
            self.stations = stations

        self.synchronize(attrs=["stations"])

        return (True)


    @_utilities.log_errors(logger)
    def load_velocity_models(self):
        """
        Parse and return velocity models from file.

        ROOT_RANK parses file and broadcasts contents to all other
        processes.
        """

        logger.info("Loading velocity models.")

        if RANK == ROOT_RANK:

            # Parse velocity model files.
            velocity_models = _dataio.parse_velocity_models(self.cfg)
            self.pwave_model, self.swave_model, self.psr_model = velocity_models
            self.step_size = self.pwave_model.step_size

        self.synchronize(attrs=["pwave_model", "swave_model","psr_model", "step_size"])

        return (True)


    @_utilities.log_errors(logger)
    @_utilities.root_only(RANK)
    def purge_raypaths(self):
        """
        Destroys all stored raypaths.
        """

        logger.debug("Purging raypath directory.")

        shutil.rmtree(self.raypath_dir)
        os.makedirs(self.raypath_dir)

        return (True)

    @_utilities.log_errors(logger)
    def relocate_events(self, method):
        if method == "LINEAR":
            self._relocate_events_linear()
        elif method == "DE":
            self._relocate_events_de()
        else:
            raise (
                ValueError(
                    "Relocation method must be either \"linear\" or \"DE\"."
                )
            )

    @_utilities.log_errors(logger)
    def _relocate_events_linear(self, niter_linloc=1):
        """
        Relocate all events based on linear inversion and update the "events" attribute.
        """

        logger.info("Relocating events with linear inversion.")
        raypath_dir = self.raypath_dir

        if RANK == ROOT_RANK:
            events = self.events.set_index("event_id")
            events["idx"] = range(len(events))
            arrivals = self.arrivals

            for iter_loc in range(niter_linloc):
                column_idxs = np.array([],dtype=int)
                nonzero_values = np.array([],dtype=float)
                nsegments = np.array([],dtype=int)
                residuals = np.array([],dtype=float)

                for phase in self.phases:
                    if phase == "P":
                        model = self.pwave_model
                    elif phase == "S":
                        model = self.swave_model

                    arrivalssub = arrivals[arrivals["phase"]==phase]
                    arrivalssub = arrivalssub.set_index(["network", "station"])
                    idx_reloc = arrivalssub.index.unique()
                    for network, station in idx_reloc:

                        _arrivals = arrivalssub.loc[(network, station)]
                        _arrivals = _arrivals.set_index("event_id")

                        filename = f"{network}.{station}.{phase}.h5"
                        path = os.path.join(raypath_dir, filename)
                        if not os.path.isfile(path):
                            continue
                        raypath_file = h5py.File(path, mode="r")

                        for event_id, arrival in _arrivals.iterrows():

                            event = events.loc[event_id]
                            idx = int(event["idx"])

                            raypath = raypath_file[phase][:, idx]
                            raypath = np.stack(raypath).T
                            if (len(raypath)) < 10:
                                continue
                            dpos = np.zeros(3,)
                            dpos[0] = raypath[-2,0]-raypath[-1,0]
                            dpos[1] = raypath[-1,0]*(raypath[-2,1]-raypath[-1,1])
                            dpos[2] = raypath[-1,0]*(raypath[-2,2]-raypath[-1,2])*np.cos(raypath[-1,1])

                            dpos = dpos/np.sqrt(np.sum(dpos**2))
                            event_coords = events.loc[event_id, ["latitude", "longitude", "depth"]]
                            event_coords = geo2sph(event_coords)
                            vel_hypo = model.value(event_coords)
                            dtdx = np.zeros(4,)
                            dtdx[:-1] = dpos/vel_hypo
                            dtdx[-1] = 1.0
                            _column_idxs = np.arange(idx*4,idx*4+4)
                            _nonnzero_values = dtdx

                            column_idxs = np.append(column_idxs, _column_idxs)
                            nonzero_values = np.append(nonzero_values,_nonnzero_values)
                            nsegments = np.append(nsegments, len(_column_idxs))
                            residuals = np.append(residuals, arrival["residual"])

                        raypath_file.close()
                row_idxs = [
                    i for i in range(len(nsegments))
                      for j in range(nsegments[i])
                ]
                row_idxs = np.array(row_idxs)

                ncol = (events["idx"].max()+1)*4

                Gmatrix = scipy.sparse.coo_matrix(
                    (nonzero_values, (row_idxs, column_idxs)),
                    shape=(len(nsegments), ncol)
                )


                # call lsmr for relocating
                # add three more parameters into cfg file,
                # "niter_linloc","damp_reloc" and "maxiter"
                atol    = self.cfg["relocate"]["atol"]
                btol    = self.cfg["relocate"]["btol"]
                conlim  = self.cfg["relocate"]["conlim"]
                damp    = self.cfg["relocate"]["damp"]
                maxiter = self.cfg["relocate"]["maxiter"]

                result = scipy.sparse.linalg.lsmr(
                    Gmatrix,
                    residuals,
                    damp,
                    atol,
                    btol,
                    conlim,
                    maxiter,
                    show=False
                )
                x, istop, itn, normr, normar, norma, conda, normx = result

                # change rad to degree
                drad = x[::4]
                dlat = x[1::4]*180.0/(np.pi*(_constants.EARTH_RADIUS-events["depth"]))
                dlon = x[2::4]*180.0/(np.pi*(_constants.EARTH_RADIUS-events["depth"]))/np.cos(np.radians(events["latitude"]))
                dorigin = x[3::4]

                #update events
                events["latitude"] = events["latitude"]+dlat
                events["longitude"] = events["longitude"]-dlon
                events["depth"] = events["depth"]+drad
                events["time"] = events["time"]+dorigin
            events = events.reset_index()
            self.events = events
        self.synchronize(attrs=["events"])
        return (True)

    @_utilities.log_errors(logger)
    def _relocate_events_de(self):
        """
        Relocate all events and update the "events" attribute.
        """

        logger.info("Relocating events.")

        if RANK == ROOT_RANK:
            ids = self.events.astype({'event_id':str})["event_id"]
            
            self._dispatch(sorted(ids))

            logger.debug("Dispatch complete. Gathering events.")
            
            # Gather and concatenate events from all workers.
            events = COMM.gather(None, root=ROOT_RANK)
            
            events = pd.concat(events, ignore_index=True)
            
            events = events.sort_values(by="event_id",ignore_index=True).astype({
                "latitude":float,
                "longitude":float,
                "depth":float,
                "time":float,
                "residual":float,
                "event_id":int,
                "sampling_count": int
            })














            
            self.events = events

        else:
            # Define columns to output.
            columns = [
                "latitude",
                "longitude",
                "depth",
                "time",
                "residual",
                "event_id",
                "sampling_count"
            ]


            # Initialize EQLocator object.
            _path = self.traveltime_inventory_path
            _station_dict = station_dict(self.stations)

            with pykonal.locate.EQLocator(_path) as locator:

                # Create some aliases for configuration-file parameters.
                depth_min = self.cfg["relocate"]["depth_min"]
                dlat = self.cfg["relocate"]["dlat"]
                dlon = self.cfg["relocate"]["dlon"]
                dz = self.cfg["relocate"]["ddepth"]
                dt = self.cfg["relocate"]["dtime"]

                # Convert configuration-file parameters from geographic to
                # spherical coordinates
                rho_max = _constants.EARTH_RADIUS - depth_min
                dtheta = np.radians(dlat)
                dphi = np.radians(dlon)

                # Initialize the search region.
                delta = np.array([dz, dtheta, dphi, dt])

                events = self.events.astype({"event_id": str})
                events = events.set_index("event_id")

                # Initialize empty DataFrame for updated event locations.
                relocated_events = pd.DataFrame()

                while True:

                    # Request an event
                    event_id = self._request_dispatch()
                    if event_id is None:
                        
                        logger.debug("Received sentinel, gathering events.")
                        COMM.gather(relocated_events, root=ROOT_RANK)

                        break

                    logger.debug(f"Received event ID #{event_id}")

                    # Extract the initial event location and convert to
                    # spherical coordinates.
                    _columns = ["latitude", "longitude", "depth", "time"]
                    
                    event = events.loc[str(event_id)]
                    initial = event[_columns].values
                    initial[:3] = geo2sph(initial[:3])

                    # Clear previous event's arrivals from EQLocator.
                    locator.clear_arrivals()

                    # Update EQLocator with arrivals for this event.
                    _arrivals = arrival_dict(self.arrivals.astype({"event_id": str}), str(event_id))
                    locator.add_arrivals(_arrivals)
                    initial = np.array(list(initial))
                    # Relocate the event.
                    loc = locator.locate(initial, delta)
                    
                    loc[0] = min(loc[0], rho_max)

                    # Get residual RMS, reformat result, and append to
                    # relocated_events DataFrame.
                    rms = locator.rms(loc)
                    loc[:3] = sph2geo(loc[:3])
                    event = pd.DataFrame(
                        [np.concatenate((loc, [rms, str(event_id), int(event.sampling_count)]))],
                        columns=columns
                    )
                    relocated_events = pd.concat([relocated_events,event], ignore_index=True)
#                    print(len(relocated_events))
#                    print(event)

        self.synchronize(attrs=["events"])

        return (True)



    @_utilities.log_errors(logger)
    def sanitize_data(self):
        """
        Sanitize input data.
        """

        logger.info("Sanitizing data.")

        if RANK == ROOT_RANK:

            # Drop duplicate stations.
            keys = ["network", "station"]
            n0 = len(self.stations)
            self.stations = self.stations.drop_duplicates(keys)
            dn = n0 - len(self.stations)
            if dn > 0:
                logger.info(
                    f"Dropped {dn} event{'s' if dn > 1 else ''} duplicate "
                    f"stations."
                )

            # Drop duplicate arrivals.
            keys = ["network", "station", "phase", "event_id"]
            n0 = len(self.arrivals)
            self.arrivals = self.arrivals.drop_duplicates(keys)
            dn = n0 - len(self.arrivals)
            if dn > 0:
                logger.info(
                    f"Dropped {dn} event{'s' if dn > 1 else ''} duplicate "
                    f"arrivals."
                )

            # Drop events without minimum number of arrivals
            min_narrival = self.cfg["algorithm"]["min_narrival"]
            n0 = len(self.events)
            counts = self.arrivals["event_id"].value_counts()
            counts = counts[counts >= min_narrival]
            event_ids = counts.index
            self.events = self.events[self.events["event_id"].isin(event_ids)]
            dn = n0 - len(self.events)
            if dn > 0:
                logger.info(
                    f"Dropped {dn} event{'s' if dn > 1 else ''} with < "
                    f"{min_narrival} arrivals."
                )

            # Drop arrivals without events.
            n0 = len(self.arrivals)
            bool_idx = self.arrivals["event_id"].isin(self.events["event_id"])
            self.arrivals = self.arrivals[bool_idx]
            dn = n0 - len(self.arrivals)
            if dn > 0:
                logger.info(
                    f"Dropped {dn} arrival{'s' if dn > 1 else ''} "
                    f"without associated events."
                )

            # Drop stations without arrivals.
            n0 = len(self.stations)
            arrivals = self.arrivals.set_index(["network", "station"])
            idx_keep = arrivals.index.unique()
            stations = self.stations.set_index(["network", "station"])
            stations = stations.loc[idx_keep]
            stations = stations.reset_index()
            self.stations = stations
            dn = n0 - len(self.stations)
            if dn > 0:
                logger.info(
                    f"Dropped {dn} station{'s' if dn > 1 else ''} without "
                    f"associated arrivals."
                )

            # Drop arrivals without stations.
            n0 = len(self.arrivals)
            stations = self.stations.set_index(["network", "station"])
            idx_keep = stations.index.unique()
            arrivals = self.arrivals.set_index(["network", "station"])
            arrivals = arrivals.loc[idx_keep]
            arrivals = arrivals.reset_index()
            self.arrivals = arrivals
            dn = n0 - len(self.arrivals)
            if dn > 0:
                logger.info(
                    f"Dropped {dn} arrival{'s' if dn > 1 else ''} without "
                    f"associated stations."
                )



        self.synchronize(attrs=["stations"])

        return (True)


    @_utilities.log_errors(logger)
    @_utilities.root_only(RANK)
    def save_events(self):
        """
        Save the current "events", and "arrivals" to and HDF5 file using
        pandas.HDFStore.
        """

        logger.info(f"Saving event data from iteration #{self.iiter}")

        path = os.path.join(self.argc.output_dir, f"{self.iiter:02d}")

        events       = self.events
        EVENT_DTYPES = _constants.EVENT_DTYPES
        for column in EVENT_DTYPES:

            events[column] = events[column].astype(EVENT_DTYPES[column])

        arrivals       = self.arrivals
        ARRIVAL_DTYPES = _constants.ARRIVAL_DTYPES
        for column in ARRIVAL_DTYPES:
            arrivals[column] = arrivals[column].astype(ARRIVAL_DTYPES[column])

        events.to_hdf(f"{path}.events.h5", key="events")
        arrivals.to_hdf(f"{path}.events.h5", key="arrivals")

        return(True)


    @_utilities.log_errors(logger)
    @_utilities.root_only(RANK)
    def save_model(self, phase: str) -> bool:
        """
        Save model data to disk for single phase.

        Return True upon successful completion.
        """

        logger.info(f"Saving {phase}-wave model for iteration #{self.iiter}")

        phase = phase.lower()
        path = os.path.join(self.argc.output_dir, f"{self.iiter:02d}")

        handle = f"{phase}wave_model"
        model = getattr(self, handle)
        model.to_hdf(path + f".{handle}.h5")

        if phase=='s':
            handle = f"psr_model"
            model = getattr(self, handle)
            model.to_hdf(path + f".{handle}.h5")
            
        if self.iiter == 0:

            return (True)

        handle = f"{phase}wave_variance"
        model = getattr(self, handle)
        model.to_hdf(path + f".{handle}.h5")

        if phase=='s':
            handle = f"psr_variance"
            model = getattr(self, handle)
            model.to_hdf(path + f".{handle}.h5")
            
        if self.argc.output_realizations is True:
            handle = f"{phase}wave_realization_stack"
            stack = getattr(self, handle)
            with h5py.File(path + f".{handle}.h5", mode="w") as f5:
                f5.create_dataset(
                    f"{phase}wave_stack",
                    data=stack[:]
                )

        return (True)


    @_utilities.log_errors(logger)
    def synchronize(self, attrs="all"):
        """
        Synchronize input data across all processes.

        "attrs" may be an iterable of attribute names to synchronize.
        """


        _all = (
            "arrivals",
            "cfg",
            "events",
            "projection_matrix",
            "pwave_model",
            "swave_model",
            "psr_model",
            "sampled_arrivals",
            "stations",
            "step_size",
            "voronoi_cells"
        )

        if attrs == "all":
            attrs = _all

        for attr in attrs:
            value = getattr(self, attr) if RANK == ROOT_RANK else None
            value = COMM.bcast(value, root=ROOT_RANK)
            setattr(self, attr, value)

        COMM.barrier()

        return (True)


    @_utilities.log_errors(logger)
    def update_arrival_residuals(self):
        """
        Compute arrival-time residuals based on current event locations
        and velocity models, and update "residual" columns of "arrivals"
        attribute.
        """

        logger.info("Updating arrival residuals.")

        arrivals = self.arrivals.astype({"event_id":str}).set_index(["network", "station", "phase"])
        arrivals = arrivals.sort_index()
        if RANK == ROOT_RANK:
            ids = arrivals.index.unique()
            self._dispatch(ids)
            logger.debug("Dispatch complete. Gathering arrivals.")
            arrivals = COMM.gather(None, root=ROOT_RANK)
            arrivals = pd.concat(arrivals, ignore_index=True)
            arrivals = arrivals.convert_dtypes()
            self.arrivals = arrivals

        else:

            events = self.events.astype({"event_id":str}).set_index("event_id")
            updated_arrivals = pd.DataFrame()

            last_handle = None

            _path = self.traveltime_inventory_path
            with TraveltimeInventory(_path, mode="r") as traveltime_inventory:

                while True:

                    # Request an event
                    item = self._request_dispatch()

                    if item is None:
                        logger.debug("Received sentinel. Gathering arrivals.")
                        COMM.gather(updated_arrivals, root=ROOT_RANK)

                        break


                    network, station, phase = item
                    handle = "/".join([network, station, phase])

                    if handle != last_handle:

                        traveltime = traveltime_inventory.read(handle)
                        last_handle = handle

                    _arrivals = arrivals.loc[(network, station, phase)]
                    # print("________")
                    # print(_arrivals.dtypes)
                    # print(events.dtypes)

                
                    _events = events.loc[_arrivals["event_id"].values]
                
                    arrival_times = _arrivals["time"].values

                    origin_times = _events["time"].astype(float).values
                    coords = _events[["latitude", "longitude", "depth"]].values
                    coords = geo2sph(coords)
                    residuals = arrival_times - (origin_times + traveltime.resample(coords))
                    _arrivals = dict(
                        network=network,
                        station=station,
                        phase=phase,
                        event_id=_arrivals["event_id"].values,
                        time=arrival_times,
                        residual=residuals
                    )
                    _arrivals = pd.DataFrame(_arrivals)
                    updated_arrivals = pd.concat([updated_arrivals,_arrivals], ignore_index=True)

        self.synchronize(attrs=["arrivals"])

        return (True)

    @_utilities.log_errors(logger)
    def update_model(self, phase):
        """
        Stack random realizations to obtain average model and update
        appropriate attributes.
        """

        phase = phase.lower()

        if RANK == ROOT_RANK:

            handle = f"{phase}wave_realization_stack"
            stack = getattr(self, handle)
            values = np.median(stack, axis=0)
            variance = np.var(stack, axis=0)

            handle = f"{phase}wave_model"
            model = getattr(self, handle)
            model.values = values

            handle = f"{phase}wave_variance"
            model = getattr(self, handle)
            model.values = variance

            if phase=="s":
                stackps = getattr(self, "psr_realization_stack")
#                stackps = stackp.values/stack.values
                valuesps = np.median(stackps, axis=0)
                varianceps = np.var(stackps, axis=0)
                
                handleps = f"psr_model"
                modelpsr = getattr(self, handleps)
                modelpsr.values = valuesps
                
                handle = f"psr_variance"
                modelpsr = getattr(self, handle)
                modelpsr.values = varianceps
                
        
        attrs = [f"{phase}wave_model"]
        if phase=="s":
            attrs.append("psr_model")
            
        self.synchronize(attrs=attrs)

        return (True)



@_utilities.log_errors(logger)
def arrival_dict(dataframe, event_id):
    """
    Return a dictionary with phase-arrival data suitable for passing to
    the EQLocator.add_arrivals() method.

    Returned dictionary has ("station_id", "phase") keys, where
    "station_id" = f"{network}.{station}", and values are
    phase-arrival timestamps.
    """

    dataframe = dataframe.set_index("event_id")
    fields = ["network", "station", "phase", "time"]
    dataframe = dataframe.loc[event_id, fields]

    _arrival_dict = {
        (network, station, phase): timestamp
        for network, station, phase, timestamp in dataframe.values
    }

    return (_arrival_dict)

def remove_outliers(dataframe, tukey_k, column, max_resid=0):
    """
    Return DataFrame with outliers removed using Tukey fences.
    """
    if max_resid!=0:
        dataframe = dataframe[
                 ((dataframe[column] <= max_resid) & (dataframe[column] >= -max_resid)) |
                 (dataframe[column].isna())
                              ]
    if tukey_k:
        q1, q3 = dataframe[column].quantile(q=[0.25, 0.75])
        iqr = q3 - q1
        vmin = q1 - tukey_k * iqr
        vmax = q3 + tukey_k * iqr
        dataframe = dataframe[
             (dataframe[column] > vmin)
            &(dataframe[column] < vmax)
        ]

    return (dataframe)
    
# def remove_outliers(dataframe, tukey_k, column):
#     """
#     Return DataFrame with outliers removed using Tukey fences.
#     """

#     q1, q3 = dataframe[column].quantile(q=[0.25, 0.75])
#     iqr = q3 - q1
#     vmin = q1 - tukey_k * iqr
#     vmax = q3 + tukey_k * iqr
#     dataframe = dataframe[
#          (dataframe[column] > vmin)
#         &(dataframe[column] < vmax)
#     ]

#     return (dataframe)


@_utilities.log_errors(logger)
def station_dict(dataframe):
    """
    Return a dictionary with network geometry suitable for passing to
    the EQLocator constructor.

    Returned dictionary has "station_id" keys, where "station_id" =
    f"{network}.{station}", and values are spherical coordinates of
    station locations.
    """

    if np.any(dataframe[["network", "station"]].duplicated()):
        raise (IOError("Multiple coordinates supplied for single station(s)"))

    dataframe = dataframe.set_index(["network", "station"])

    _station_dict = {
        (network, station): geo2sph(
            dataframe.loc[
                (network, station),
                ["latitude", "longitude", "depth"]
            ].values
        ) for network, station in dataframe.index
    }

    return (_station_dict)

def dist_on_unit_sphere(lat1, lon1, lat2, lon2):
    # Convert latitude and longitude from decimal degrees to radians
    phi1 = np.radians(lat1)
    phi2 = np.radians(lat2)
    theta1 = np.radians(lon1)
    theta2 = np.radians(lon2)

    # Calculate the spherical distance from the law of cosines
    dtheta = theta2 - theta1
    delta_phi = phi2 - phi1
    a = np.sin(delta_phi/2)**2 + np.cos(phi1) * np.cos(phi2) * np.sin(dtheta/2)**2
    c = 2 * np.arctan2(np.sqrt(a), np.sqrt(1-a))
    return np.degrees(c)

def dist_km(lat1, lon1, lat2, lon2):
    # Convert latitude and longitude from decimal degrees to radians
    phi1 = np.radians(lat1)
    phi2 = np.radians(lat2)
    delta_phi = np.radians(lat2 - lat1)
    delta_lon = np.radians(lon2 - lon1)

    # Calculate the spherical distance using the Haversine formula
    a = np.sin(delta_phi/2)**2 + np.cos(phi1) * np.cos(phi2) * np.sin(delta_lon/2)**2
    distance = 6371 * 2 * np.arctan2(np.sqrt(a), np.sqrt(1-a))
    return distance

def get_gap(tmp):
    # Finds the largest station coverage gap around an event
    sorted_azimuths = tmp.sort_values(by="azimuth").copy()
    sorted_azimuths["Diff"] = np.nan
    sorted_azimuths.loc[sorted_azimuths.index,"Diff"] = sorted_azimuths.azimuth.diff().dropna()
    sorted_azimuths.loc[sorted_azimuths.index[0],"Diff"] = circular_gap = 360 - (sorted_azimuths.iloc[-1].azimuth - sorted_azimuths.iloc[0].azimuth)
    return sorted_azimuths["Diff"].max()

def sample_some_arrivals(df, n):
    if n>=len(df):
        return df
    else:
        return df.sample(n=n, replace=False, weights='weight')
