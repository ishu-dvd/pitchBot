from __future__ import annotations

import os
import platform

from pitchbot.benchmarks.models import HardwareProfile


def capture_hardware_profile() -> HardwareProfile:
    return HardwareProfile(
        operating_system=platform.platform(),
        architecture=platform.machine() or "unknown",
        python_version=platform.python_version(),
        processor=platform.processor() or "unknown",
        logical_cpu_count=os.cpu_count(),
    )
