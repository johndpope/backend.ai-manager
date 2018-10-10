from decimal import Decimal
from pathlib import Path

import pytest

from ai.backend.gateway.exceptions import ImageNotFound


@pytest.fixture
async def image_aliases(config_server, tmpdir):
    content = '''
aliases:
  - ['my-python',     'test-python:latest']
  - ['my-python:3.6', 'test-python:3.6-debian']  # preferred
'''
    p = Path(tmpdir) / 'test-image-aliases.yml'
    p.write_text(content)

    yield p

    await config_server.etcd.delete('images/_aliases/my-python')
    await config_server.etcd.delete('images/_aliases/my-python:3.6')


@pytest.fixture
async def volumes(config_server, tmpdir):
    content = '''
volumes:
  - name: test-aws-shard-1
    mount:
      at: requested
      fstype: nfs
      path: "...efs...."
      options: "..."
'''
    p = Path(tmpdir) / 'test-image-aliases.yml'
    p.write_text(content)

    yield p

    await config_server.etcd.delete_prefix('volumes/test-aws-shard-1')


class TestConfigServer:

    @pytest.mark.asyncio
    async def test_register_myself(self, app, config_server):
        await config_server.register_myself(app['config'])

        assert await config_server.etcd.get('nodes/manager')
        assert await config_server.etcd.get('nodes/redis')
        assert await config_server.etcd.get('nodes/manager/event_addr')

    @pytest.mark.asyncio
    async def test_deregister_myself(self, app, config_server):
        await config_server.register_myself(app['config'])
        await config_server.deregister_myself()

        data = list(await config_server.etcd.get_prefix('nodes/manager'))
        assert len(data) == 0

    @pytest.mark.asyncio
    async def test_update_kernel_images_from_file(self, config_server,
                                                  image_metadata):
        name = 'test-python'
        img_data = list(await config_server.etcd.get_prefix(f'images/{name}'))
        assert 0 == len(img_data)

        await config_server.update_kernel_images_from_file(image_metadata)

        img_data = list(await config_server.etcd.get_prefix(f'images/{name}'))
        assert (f'images/{name}', '1') in img_data
        assert (f'images/{name}/cpu', '1.00') in img_data
        assert (f'images/{name}/mem', '1.00') in img_data
        assert (f'images/{name}/gpu', '0.00') in img_data
        assert (f'images/{name}/tags/3.6-debian', 'ca7b9f52b6c2') in img_data
        alias_data = list(await config_server.etcd.get_prefix(f'images/_aliases'))
        assert (f'images/_aliases/{name}:latest', f'{name}:3.6-debian') in alias_data

    @pytest.mark.asyncio
    async def test_update_aliases_from_file(self, config_server, image_aliases):
        await config_server.update_aliases_from_file(Path(image_aliases))
        alias_data = list(await config_server.etcd.get_prefix('images/_aliases'))
        assert ('images/_aliases/my-python', 'test-python:latest') in alias_data
        assert ('images/_aliases/my-python:3.6', 'test-python:3.6-debian') in alias_data  # noqa

    @pytest.mark.asyncio
    async def test_update_volumes_from_file(self, config_server, volumes):
        name = 'test-aws-shard-1'

        img_data = list(await config_server.etcd.get_prefix(f'volumes/{name}/mount'))
        assert 0 == len(img_data)

        await config_server.update_volumes_from_file(volumes)

        img_data = list(await config_server.etcd.get_prefix(f'volumes/{name}/mount'))
        assert 4 == len(img_data)

    @pytest.mark.asyncio
    async def test_get_overbook_factors(self, config_server):
        ret = await config_server.get_overbook_factors()
        assert 'cpu' in ret
        assert 'mem' in ret
        assert 'gpu' in ret

    @pytest.mark.asyncio
    async def test_get_image_required_slots(self, config_server):
        name = 'test-python'
        tag = ''
        await config_server.etcd.put(f'images/{name}', 1)
        await config_server.etcd.put(f'images/{name}/cpu', '1.00')
        await config_server.etcd.put(f'images/{name}/mem', '1.00')
        await config_server.etcd.put(f'images/{name}/gpu', '0.00')

        try:
            ret = await config_server.get_image_required_slots(name, tag)
        finally:
            await config_server.etcd.delete_prefix(f'images/{name}')

        assert ret.cpu == Decimal('1')
        assert ret.mem == Decimal('1')
        assert ret.gpu == Decimal('0')

    @pytest.mark.asyncio
    async def test_get_image_metadata_all(self, config_server,
                                          image_metadata):
        name = 'test-python'
        tag = '3.6-debian'
        await config_server.update_kernel_images_from_file(image_metadata)

        metadata = await config_server.get_image_metadata()
        assert name in metadata
        assert Decimal(metadata[name]['cpu']) == Decimal('1')
        assert Decimal(metadata[name]['mem']) == Decimal('1')
        assert Decimal(metadata[name]['gpu']) == Decimal('0')
        assert metadata[name]['hash'][tag] == 'ca7b9f52b6c2'

    @pytest.mark.asyncio
    async def test_get_image_metadata_one(self, config_server,
                                          image_metadata):
        wrong_name = 'not-existing-image-name'
        name = 'test-python'
        tag = '3.6-debian'
        await config_server.update_kernel_images_from_file(image_metadata)

        try:
            metadata = await config_server.get_image_metadata(wrong_name)
        except ImageNotFound:
            pass

        metadata = await config_server.get_image_metadata(name)
        assert Decimal(metadata['cpu']) == Decimal('1')
        assert Decimal(metadata['mem']) == Decimal('1')
        assert Decimal(metadata['gpu']) == Decimal('0')
        assert metadata['hash'][tag] == 'ca7b9f52b6c2'
        assert len(metadata['hash'].keys()) == 1

    @pytest.mark.asyncio
    async def test_resolve_image_name(self, config_server,
                                      image_metadata, image_aliases):
        await config_server.update_kernel_images_from_file(image_metadata)
        await config_server.update_aliases_from_file(image_aliases)
        # lookup with metadata
        ret = await config_server.resolve_image_name('test-python')
        assert ret == ('test-python', '3.6-debian')
        ret = await config_server.resolve_image_name('test-python:3.6-debian')
        assert ret == ('test-python', '3.6-debian')
        ret = await config_server.resolve_image_name('test-python:latest')
        assert ret == ('test-python', '3.6-debian')
        # lookup with aliases
        ret = await config_server.resolve_image_name('my-python')
        assert ret == ('test-python', '3.6-debian')
        ret = await config_server.resolve_image_name('my-python:3.6')
        assert ret == ('test-python', '3.6-debian')
        # lookup with non-existent name/tags
        with pytest.raises(ImageNotFound):
            await config_server.resolve_image_name('test-python:xyz')
        with pytest.raises(ImageNotFound):
            await config_server.resolve_image_name('my-python:xyz')
