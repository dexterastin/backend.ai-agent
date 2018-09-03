from unittest import mock
from decimal import Decimal
import io
import json
from pathlib import Path

import pytest
import attr

from ai.backend.agent.vendor import linux
from ai.backend.agent.accelerator import AbstractAcceleratorInfo
from ai.backend.agent.resources import (
    CPUAllocMap, KernelResourceSpec,
    AcceleratorAllocMap,
    Mount, MountPermission,
)


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


@attr.s(auto_attribs=True)
class DummyAcceleratorInfo(AbstractAcceleratorInfo):

    unit_memory = 1 * (2 ** 20)  # 1 MiB
    unit_proc = 3

    def max_share(self):
        q = Decimal('.01')
        return min(Decimal(self.memory_size / type(self).unit_memory).quantize(q),
                   Decimal(self.processing_units / type(self).unit_proc).quantize(q))

    def share_to_spec(self, share) -> (int, int):
        return (share * type(self).unit_memory,
                share * type(self).unit_proc)

    def spec_to_share(self, req_mem, req_proc) -> Decimal:
        return max(req_mem / type(self).unit_memory,
                   req_proc / type(self).unit_proc)


class TestAcceleratorAllocMap:

    @pytest.fixture
    def dummy_devices(self):
        return [
            DummyAcceleratorInfo('d1', '00:01', 0, 2 * (2 ** 20), 9),
            DummyAcceleratorInfo('d2', '08:01', 1, 1 * (2 ** 20), 3),
        ]

    @pytest.fixture
    def dummy_devices_in_same_node(self):
        return [
            DummyAcceleratorInfo('d1', '00:01', 0, 2 * (2 ** 20), 9),
            DummyAcceleratorInfo('d2', '00:02', 0, 1 * (2 ** 20), 3),
        ]

    def test_max_share(self, dummy_devices, dummy_devices_in_same_node):
        assert dummy_devices[0].max_share() == 2
        assert dummy_devices[1].max_share() == 1
        assert dummy_devices_in_same_node[0].max_share() == 2
        assert dummy_devices_in_same_node[1].max_share() == 1

    def test_alloc_free_within_limits(self, dummy_devices):
        alloc_map = AcceleratorAllocMap(dummy_devices, None)
        assert alloc_map.device_shares['d1'].normalize() == Decimal('0')
        assert alloc_map.device_shares['d2'].normalize() == Decimal('0')

        node, dev_shares = alloc_map.alloc(Decimal('0.5'))
        assert node == 0
        assert dev_shares == {'d1': Decimal('0.5')}
        assert alloc_map.device_shares['d1'].normalize() == Decimal('0.5')
        assert alloc_map.device_shares['d2'].normalize() == Decimal('0')

        alloc_map.free(dev_shares)
        assert alloc_map.device_shares['d1'].normalize() == Decimal('0')
        assert alloc_map.device_shares['d2'].normalize() == Decimal('0')

    def test_alloc_free_multi_devices(self, dummy_devices_in_same_node):
        alloc_map = AcceleratorAllocMap(dummy_devices_in_same_node, None)

        node, dev_shares = alloc_map.alloc(Decimal('2.5'))
        assert node == 0
        assert dev_shares == {'d1': Decimal('2.0'), 'd2': Decimal('0.5')}
        assert alloc_map.device_shares['d1'].normalize() == Decimal('2.0')
        assert alloc_map.device_shares['d2'].normalize() == Decimal('0.5')

        alloc_map.free(dev_shares)
        assert alloc_map.device_shares['d1'].normalize() == Decimal('0')
        assert alloc_map.device_shares['d2'].normalize() == Decimal('0')

    def test_alloc_free_across_numa_nodes(self, dummy_devices):
        alloc_map = AcceleratorAllocMap(dummy_devices, None)

        node, dev_shares = alloc_map.alloc(Decimal('1.5'))
        assert node == 0
        assert dev_shares == {'d1': Decimal('1.5')}
        assert alloc_map.device_shares['d1'].normalize() == Decimal('1.5')
        assert alloc_map.device_shares['d2'].normalize() == Decimal('0')

        node, dev_shares = alloc_map.alloc(Decimal('1.0'))
        assert node == 1
        assert dev_shares == {'d2': Decimal('1.0')}
        assert alloc_map.device_shares['d1'].normalize() == Decimal('1.5')
        assert alloc_map.device_shares['d2'].normalize() == Decimal('1.0')

        alloc_map.free({'d1': Decimal('1.5')})
        assert alloc_map.device_shares['d1'].normalize() == Decimal('0')
        assert alloc_map.device_shares['d2'].normalize() == Decimal('1.0')

        node, dev_shares = alloc_map.alloc(Decimal('1.0'))
        assert node == 0
        assert dev_shares == {'d1': Decimal('1.0')}
        assert alloc_map.device_shares['d1'].normalize() == Decimal('1.0')
        assert alloc_map.device_shares['d2'].normalize() == Decimal('1.0')

    def test_alloc_free_above_limits(self, dummy_devices):
        alloc_map = AcceleratorAllocMap(dummy_devices, None)
        with pytest.raises(RuntimeError):
            _, _ = alloc_map.alloc(Decimal('2.5'))
        assert alloc_map.device_shares['d1'].normalize() == Decimal('0')
        assert alloc_map.device_shares['d2'].normalize() == Decimal('0')


class TestKernelResourceSpec:

    @pytest.fixture
    def sample_resource_spec(self):
        return KernelResourceSpec(
            numa_node=99,
            cpu_set={1, 4, 9},
            memory_limit=128 * (2**20),
            scratch_disk_size=91124,
            shares={
                '_cpu': Decimal('0.47'),
                '_mem': Decimal('1.21'),
                '_gpu': Decimal('0.53'),
                'cuda': {
                    2: Decimal('0.33'),
                    5: Decimal('0.2'),
                },
            },
            mounts=[
                Mount(Path('/home/user/hello.txt'),
                      Path('/home/work/hello.txt'),
                      MountPermission.READ_ONLY),
                Mount(Path('/home/user/world.txt'),
                      Path('/home/work/world.txt'),
                      MountPermission.READ_WRITE),
            ],
        )

    def test_write_read_equality(self, sample_resource_spec):
        buffer = io.StringIO()
        sample_resource_spec.write_to_file(buffer)
        buffer.seek(0, io.SEEK_SET)
        read_spec = KernelResourceSpec.read_from_file(buffer)
        assert read_spec == sample_resource_spec

    def test_to_json(self, sample_resource_spec):
        o = json.loads(sample_resource_spec.to_json())
        assert o['cpu_set'] == [1, 4, 9]
        assert o['numa_node'] == 99
        assert o['shares']['_cpu'] == '0.47'
        assert o['shares']['_mem'] == '1.21'
        assert o['shares']['_gpu'] == '0.53'
        assert o['shares']['cuda']['2'] == '0.33'
        assert o['shares']['cuda']['5'] == '0.2'
        assert o['mounts'][0] == '/home/user/hello.txt:/home/work/hello.txt:ro'
        assert o['mounts'][1] == '/home/user/world.txt:/home/work/world.txt:rw'
