"""Tests for private protobuf descriptor pool isolation."""

from pathlib import Path
import subprocess
import sys

from custom_components.nest_legacy.pynest import proto_pool
from custom_components.nest_legacy.pynest.protobuf_gen.nestlabs.gateway import v2_pb2

_REPO_ROOT = Path(__file__).resolve().parents[4]


def _run_python(script: str) -> None:
    """Run a snippet in a fresh Python interpreter from the repository root."""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"Subprocess failed (exit {result.returncode}):\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


def test_importing_all_generated_protos_leaves_default_pool_clean() -> None:
    """Importing every generated *_pb2 module must not pollute descriptor_pool.Default()."""
    _run_python(
        """
import importlib
from pathlib import Path
from google.protobuf import descriptor_pool

gen_dir = Path("custom_components/nest_legacy/pynest/protobuf_gen")
default_pool = descriptor_pool.Default()
imported = []

for path in sorted(gen_dir.rglob("*_pb2.py")):
    rel = path.relative_to(gen_dir).with_suffix("")
    mod_name = "custom_components.nest_legacy.pynest.protobuf_gen." + ".".join(rel.parts)
    mod = importlib.import_module(mod_name)
    file_name = mod.DESCRIPTOR.name
    imported.append(file_name)
    try:
        default_pool.FindFileByName(file_name)
    except KeyError:
        pass
    else:
        raise AssertionError(f"{file_name} leaked into descriptor_pool.Default()")

assert len(imported) >= 26
"""
    )


def test_import_succeeds_after_conflicting_descriptors_in_default_pool() -> None:
    """Nest Legacy imports cleanly even if another integration already registered v2.proto."""
    _run_python(
        """
from google.protobuf import descriptor_pb2, descriptor_pool

# Simulate another integration (e.g. Nest Protect Plus) registering an older
# nestlabs/gateway/v2.proto (without the status field) and weave/common.proto
# in the process-wide default pool.
default_pool = descriptor_pool.Default()

common_fdp = descriptor_pb2.FileDescriptorProto(
    name="weave/common.proto", package="weave.common", syntax="proto3"
)
common_fdp.message_type.add(name="ResourceId").field.add(
    name="resourceId", number=1, label=1, type=9
)
default_pool.AddSerializedFile(common_fdp.SerializeToString())

v2_fdp = descriptor_pb2.FileDescriptorProto(
    name="nestlabs/gateway/v2.proto", package="nestlabs.gateway.v2", syntax="proto3"
)
obs = v2_fdp.message_type.add(name="ObserveResponse")
obs.field.add(name="observeResponse", number=1, label=3, type=9)
default_pool.AddSerializedFile(v2_fdp.SerializeToString())

# Now import Nest Legacy and verify our v2.proto schema (with status field) is intact.
from custom_components.nest_legacy.pynest import client  # noqa: F401
from custom_components.nest_legacy.pynest.protobuf_gen.nestlabs.gateway import v2_pb2

msg = v2_pb2.ObserveResponse()
msg.status.code = 7
msg.status.message = "authentication failed"
parsed = v2_pb2.ObserveResponse.FromString(msg.SerializeToString())
assert parsed.status.code == 7
assert parsed.status.message == "authentication failed"
"""
    )


def test_conflicting_descriptors_succeed_after_nest_legacy_import() -> None:
    """Another integration registering v2.proto in Default() works after Nest Legacy loads."""
    _run_python(
        """
from custom_components.nest_legacy.pynest import client  # noqa: F401
from google.protobuf import descriptor_pb2, descriptor_pool

v2_fdp = descriptor_pb2.FileDescriptorProto(
    name="nestlabs/gateway/v2.proto", package="nestlabs.gateway.v2", syntax="proto3"
)
v2_fdp.message_type.add(name="ObserveResponse").field.add(
    name="observeResponse", number=1, label=3, type=9
)

fd = descriptor_pool.Default().AddSerializedFile(v2_fdp.SerializeToString())
assert fd.name == "nestlabs/gateway/v2.proto"
"""
    )


def test_add_serialized_file_is_idempotent() -> None:
    """Re-registering an identical serialized descriptor returns the same file."""
    fd = proto_pool.add_serialized_file(v2_pb2.DESCRIPTOR.serialized_pb)
    assert fd.name == "nestlabs/gateway/v2.proto"


def test_add_serialized_file_rejects_non_google_dependency_from_default_pool() -> None:
    """Missing non-google/* dependencies must never be copied from Default()."""
    _run_python(
        """
from custom_components.nest_legacy.pynest import proto_pool
from google.protobuf import descriptor_pb2, descriptor_pool

default_dep = descriptor_pb2.FileDescriptorProto(
    name="other_integration/dep.proto",
    package="other_integration",
    syntax="proto3",
)
default_dep.message_type.add(name="DepMsg").field.add(
    name="x", number=1, label=1, type=5
)
descriptor_pool.Default().AddSerializedFile(default_dep.SerializeToString())

dependent = descriptor_pb2.FileDescriptorProto(
    name="other_integration/main.proto",
    package="other_integration",
    syntax="proto3",
    dependency=["other_integration/dep.proto"],
)
dependent.message_type.add(name="MainMsg").field.add(
    name="dep",
    number=1,
    label=1,
    type=11,
    type_name=".other_integration.DepMsg",
)

try:
    proto_pool.add_serialized_file(dependent.SerializeToString())
except KeyError as err:
    assert "Non-google dependency" in str(err)
else:
    raise AssertionError("Expected KeyError for non-google dependency")
"""
    )
