import numpy as np
import functools
from .strategy import ConnectionStrategy
from .. import config, _util as _gutil
from ..exceptions import *
from ..reporting import report, warn


@config.node
class Convergence(ConnectionStrategy):
    """
    Implementation of a general convergence connectivity between
    two populations of cells (this does not work with entities)
    """

    convergence = config.attr(type=float, required=True)

    def validate(self):
        pass

    def connect(self):
        # Source and target neurons are extracted
        from_type = self.presynaptic.type
        to_type = self.postsynaptic.type
        pre = self.from_cells[from_type.name]
        post = self.to_cells[to_type.name]
        convergence = self.convergence

        pre_post = np.zeros((convergence * len(post), 2))
        for i, neuron in enumerate(post):
            connected_pre = np.random.choice(pre[:, 0], convergence, replace=False)
            range_i = range(i * convergence, (i + 1) * convergence)
            pre_post[range_i, 0] = connected_pre.astype(int)
            pre_post[range_i, 1] = neuron[0]

        self.scaffold.connect_cells(self, pre_post)


class AllToAll(ConnectionStrategy):
    """
    All to all connectivity between two neural populations
    """

    def get_region_of_interest(self, chunk):
        # All to all needs all pre chunks per post chunk.
        # Fingers crossed for out of memory errors.
        return self._get_all_pre_chunks()

    @functools.cache
    def _get_all_pre_chunks(self):
        all_ps = (ct.get_placement_set() for ct in self.presynaptic.cell_types)
        chunks = set(_gutil.ichain(ps.get_all_chunks() for ps in all_ps))
        return list(chunks)

    def connect(self, pre, post):
        for from_ps in pre.placement.values():
            fl = len(from_ps)
            for to_ps in post.placement.values():
                l = len(to_ps)
                ml = fl * l
                src_locs = np.full((ml, 3), -1)
                dest_locs = np.full((ml, 3), -1)
                src_locs[:, 0] = np.repeat(np.arange(fl), l)
                dest_locs[:, 0] = np.tile(np.arange(l), fl)
                self.connect_cells(from_ps, to_ps, src_locs, dest_locs)


class ExternalConnections(ConnectionStrategy):
    """
    Load the connection matrix from an external source.
    """

    required = ["source"]
    casts = {"format": str, "warn_missing": bool, "use_map": bool, "headers": bool}
    defaults = {
        "format": "csv",
        "headers": True,
        "use_map": False,
        "warn_missing": True,
        "delimiter": ",",
    }

    has_external_source = True

    def check_external_source(self):
        return os.path.exists(self.source)

    def get_external_source(self):
        return self.source

    def validate(self):
        if self.warn_missing and not self.check_external_source():
            src = self.get_external_source()
            warn(f"Missing external source '{src}' for '{self.name}'")

    def connect(self):
        if self.format == "csv":
            return self._connect_from_csv()

    def _connect_from_csv(self):
        if not self.check_external_source():
            src = self.get_external_source()
            raise RuntimeError(f"Missing source file '{src}' for `{self.name}`.")
        from_type = self.from_cell_types[0]
        to_type = self.to_cell_types[0]
        # Read the entire csv, skipping the headers if there are any.
        data = np.loadtxt(
            self.get_external_source(),
            skiprows=int(self.headers),
            delimiter=self.delimiter,
        )
        if self.use_map:
            emap_name = lambda t: t.placement.name + "_ext_map"
            from_gid_map = self.scaffold.load_appendix(emap_name(from_type))
            to_gid_map = self.scaffold.load_appendix(emap_name(to_type))
            from_targets = self.scaffold.get_placement_set(from_type).identifiers
            to_targets = self.scaffold.get_placement_set(to_type).identifiers
            data[:, 0] = self._map(data[:, 0], from_gid_map, from_targets)
            data[:, 1] = self._map(data[:, 1], to_gid_map, to_targets)
        self.scaffold.connect_cells(self, data)

    def _map(self, data, map, targets):
        # Create a dict with pairs between the map and the target values
        # Vectorize its dictionary lookup and perform the vector function on the data
        try:
            return np.vectorize(dict(zip(map, targets)).get)(data)
        except TypeError:
            raise SourceQualityError("Missing GIDs in external map.")
