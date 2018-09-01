from unittest import mock

from ai.backend.agent.vendor import linux
from ai.backend.agent.resources import CPUAllocMap


class TestLibNuma:
    def test_node_of_cpu(self):
        numa = linux.libnuma()

        # When NUMA is not supported.
        linux._numa_supported = False
        assert numa.node_of_cpu(5) == 0

        # When NUMA is supported.
        original_numa_supported = linux._numa_supported
        linux._numa_supported = True
        with mock.patch.object(linux, '_libnuma', create=True) \
                as mock_libnuma:
            numa.node_of_cpu(5)
            mock_libnuma.numa_node_of_cpu.assert_called_once_with(5)

        linux._numa_supported = original_numa_supported

    def test_num_nodes(self):
        numa = linux.libnuma()

        # When NUMA is not supported.
        linux._numa_supported = False
        assert numa.num_nodes() == 1

        # When NUMA is supported.
        original_numa_supported = linux._numa_supported
        linux._numa_supported = True
        with mock.patch.object(linux, '_libnuma', create=True) \
                as mock_libnuma:
            numa.num_nodes()
            mock_libnuma.numa_num_configured_nodes.assert_called_once_with()

        linux._numa_supported = original_numa_supported

    def test_get_available_cores_without_docker(self, monkeypatch):
        def mock_sched_getaffinity(pid):
            raise AttributeError

        def mock_requnix_session():
            raise OSError

        numa = linux.libnuma()
        monkeypatch.setattr(linux.requnix, 'Session', mock_requnix_session,
                            raising=False)
        monkeypatch.setattr(linux.os, 'sched_getaffinity',
                            mock_sched_getaffinity,
                            raising=False)
        monkeypatch.setattr(linux.os, 'cpu_count', lambda: 4)

        numa.get_available_cores.cache_clear()
        assert numa.get_available_cores() == {0, 1, 2, 3}

        def mock_sched_getaffinity2(pid):
            return {0, 1}

        monkeypatch.setattr(linux.os, 'sched_getaffinity',
                            mock_sched_getaffinity2,
                            raising=False)

        numa.get_available_cores.cache_clear()
        assert numa.get_available_cores() == {0, 1}

    def test_get_core_topology(self, mocker):
        mocker.patch.object(linux.libnuma, 'num_nodes', return_value=2)
        mocker.patch.object(linux.libnuma, 'get_available_cores',
                            return_value={1, 2, 5})
        mocker.patch.object(linux.libnuma, 'node_of_cpu', return_value=1)

        numa = linux.libnuma()
        assert numa.get_core_topology() == ([], [1, 2, 5])


class TestCPUAllocMap:
    def get_cpu_alloc_map(self, num_nodes=1, num_cores=1):
        core_topo = tuple([] for _ in range(num_nodes))
        for c in {_ for _ in range(num_cores)}:
            n = c % num_nodes
            core_topo[n].append(c)
        avail_cores = {n for n in range(num_cores)}

        with mock.patch.object(linux.libnuma, 'get_core_topology',
                               return_value=core_topo):
            with mock.patch.object(linux.libnuma, 'get_available_cores',
                                   return_value=avail_cores):
                with mock.patch.object(linux.libnuma, 'num_nodes',
                                       return_value=num_nodes):
                    return CPUAllocMap()

    def test_cpu_alloc_map_initialization(self):
        cpu_alloc_map = self.get_cpu_alloc_map(num_nodes=3, num_cores=4)

        assert cpu_alloc_map.core_topo == ([0, 3], [1], [2])
        assert cpu_alloc_map.num_cores == 4
        assert cpu_alloc_map.num_nodes == 3
        assert cpu_alloc_map.alloc_per_node == {0: 0, 1: 0, 2: 0}
        assert cpu_alloc_map.core_shares == ({0: 0, 3: 0}, {1: 0}, {2: 0})

    def test_alloc(self):
        cpu_alloc_map = self.get_cpu_alloc_map(num_nodes=3, num_cores=4)

        assert cpu_alloc_map.alloc_per_node == {0: 0, 1: 0, 2: 0}
        assert cpu_alloc_map.core_shares == ({0: 0, 3: 0}, {1: 0}, {2: 0})

        assert cpu_alloc_map.alloc(3) == (0, {0, 3})
        assert cpu_alloc_map.alloc_per_node == {0: 3, 1: 0, 2: 0}
        assert cpu_alloc_map.core_shares == ({0: 2, 3: 1}, {1: 0}, {2: 0})

        assert cpu_alloc_map.alloc(2) == (1, {1})
        assert cpu_alloc_map.alloc_per_node == {0: 3, 1: 2, 2: 0}
        assert cpu_alloc_map.core_shares == ({0: 2, 3: 1}, {1: 2}, {2: 0})

        assert cpu_alloc_map.alloc(3) == (2, {2})
        assert cpu_alloc_map.alloc_per_node == {0: 3, 1: 2, 2: 3}
        assert cpu_alloc_map.core_shares == ({0: 2, 3: 1}, {1: 2}, {2: 3})

        assert cpu_alloc_map.alloc(4) == (1, {1})  # 2nd node least populated
        assert cpu_alloc_map.alloc_per_node == {0: 3, 1: 6, 2: 3}
        assert cpu_alloc_map.core_shares == ({0: 2, 3: 1}, {1: 6}, {2: 3})

    def test_free(self):
        cpu_alloc_map = self.get_cpu_alloc_map(num_nodes=3, num_cores=4)
        cpu_alloc_map.alloc_per_node = {0: 3, 1: 6, 2: 3}
        cpu_alloc_map.core_shares = ({0: 2, 3: 1}, {1: 6}, {2: 3})

        with mock.patch.object(linux.libnuma, 'node_of_cpu', return_value=0):
            cpu_alloc_map.free({0, 3})
            assert cpu_alloc_map.alloc_per_node == {0: 1, 1: 6, 2: 3}
            assert cpu_alloc_map.core_shares == ({0: 1, 3: 0}, {1: 6}, {2: 3})

            cpu_alloc_map.free({0})
            assert cpu_alloc_map.alloc_per_node == {0: 0, 1: 6, 2: 3}
            assert cpu_alloc_map.core_shares == ({0: 0, 3: 0}, {1: 6}, {2: 3})

        with mock.patch.object(linux.libnuma, 'node_of_cpu', return_value=1):
            cpu_alloc_map.free({1})
            assert cpu_alloc_map.alloc_per_node == {0: 0, 1: 5, 2: 3}
            assert cpu_alloc_map.core_shares == ({0: 0, 3: 0}, {1: 5}, {2: 3})

        with mock.patch.object(linux.libnuma, 'node_of_cpu', return_value=2):
            cpu_alloc_map.free({2})
            assert cpu_alloc_map.alloc_per_node == {0: 0, 1: 5, 2: 2}
            assert cpu_alloc_map.core_shares == ({0: 0, 3: 0}, {1: 5}, {2: 2})
