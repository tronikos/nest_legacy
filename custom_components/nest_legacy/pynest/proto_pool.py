"""Private protobuf descriptor pool for Nest Legacy.

Generated ``*_pb2.py`` modules register their file descriptors in ``POOL``
instead of ``descriptor_pool.Default()`` so they cannot collide with other
Home Assistant integrations bundling overlapping Nest or Weave ``.proto`` files.
"""

from google.protobuf import descriptor, descriptor_pb2, descriptor_pool

POOL = descriptor_pool.DescriptorPool()


def _ensure_dependency(file_name: str) -> None:
    """Ensure a dependency is in POOL, copying ``google/*`` deps from Default()."""
    try:
        POOL.FindFileByName(file_name)
    except KeyError:
        pass
    else:
        return

    if not file_name.startswith("google/"):
        raise KeyError(
            f"Non-google dependency {file_name!r} is missing from the private pool"
        )

    default_fd = descriptor_pool.Default().FindFileByName(file_name)
    for dep in default_fd.dependencies:
        _ensure_dependency(dep.name)
    POOL.AddSerializedFile(default_fd.serialized_pb)


def add_serialized_file(serialized_file: bytes) -> descriptor.FileDescriptor:
    """Register a serialized FileDescriptorProto in POOL, copying google/* deps."""
    file_proto = descriptor_pb2.FileDescriptorProto.FromString(serialized_file)
    for dep_name in file_proto.dependency:
        _ensure_dependency(dep_name)

    return POOL.AddSerializedFile(serialized_file)
