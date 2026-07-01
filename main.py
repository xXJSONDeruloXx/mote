from __future__ import annotations

import asyncio
import os
import pwd
import shutil
import time
from typing import Any

import decky

SERVICE_NAME = "com.steampowered.CecDaemon1"
DEVICE_INTERFACE = "com.steampowered.CecDaemon1.CecDevice1"
ACTION_METHODS = {
    "volume_up": "VolumeUp",
    "volume_down": "VolumeDown",
    "mute": "Mute",
}
CEC_OBJECT_PATHS = [
    f"/com/steampowered/CecDaemon1/Devices/Cec{index}" for index in range(16)
]
COMMAND_TIMEOUT_SECONDS = 3.0
DISCOVERY_POLL_SECONDS = 2.0
DISCOVERY_POLL_INTERVAL_SECONDS = 0.2
SETUP_COMMAND_TIMEOUT_SECONDS = 90.0
SETUP_SCRIPT_RELATIVE_PATH = os.path.join("assets", "steamos-cec-bt-wake.sh")


class SessionContext:
    def __init__(
        self,
        *,
        username: str,
        busctl_path: str,
        systemctl_path: str | None,
        runuser_path: str | None,
        env_path: str | None,
        env: dict[str, str],
    ) -> None:
        self.username = username
        self.busctl_path = busctl_path
        self.systemctl_path = systemctl_path
        self.runuser_path = runuser_path
        self.env_path = env_path
        self.env = env


class DeviceInfo:
    def __init__(self, *, object_path: str, active: bool) -> None:
        self.object_path = object_path
        self.active = active


class CommandResult:
    def __init__(
        self,
        *,
        args: tuple[str, ...],
        returncode: int,
        stdout: str,
        stderr: str,
    ) -> None:
        self.args = args
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class CecError(Exception):
    def __init__(
        self,
        message: str,
        *,
        stderr: str | None = None,
        returncode: int | None = None,
    ) -> None:
        super().__init__(message)
        self.stderr = stderr or ""
        self.returncode = returncode


class Plugin:
    def __init__(self) -> None:
        self._action_lock: asyncio.Lock | None = None
        self._cached_object_path: str | None = None

    async def _main(self) -> None:
        self._ensure_action_lock()
        decky.logger.info("cec-mote backend starting")

    async def _unload(self) -> None:
        decky.logger.info("cec-mote backend shutting down")

    async def get_status(self) -> dict:
        try:
            status = await self._load_status()
        except CecError as exc:
            return {
                "ready": False,
                "active": False,
                "audioLogicalAddress": None,
                "targetLabel": None,
                "objectPath": None,
                "warning": None,
                "error": str(exc),
            }
        return {
            "ready": True,
            "active": status["active"],
            "audioLogicalAddress": status["audioLogicalAddress"],
            "targetLabel": self._target_label(status["audioLogicalAddress"]),
            "objectPath": status["objectPath"],
            "warning": status["warning"],
            "error": None,
        }

    async def volume_up(self) -> dict:
        return await self._perform_action("volume_up")

    async def volume_down(self) -> dict:
        return await self._perform_action("volume_down")

    async def mute(self) -> dict:
        return await self._perform_action("mute")

    async def get_sleep_wake_status(self) -> dict:
        return await self._run_setup_mode("verify")

    async def install_sleep_wake(self) -> dict:
        return await self._run_setup_mode("install")

    async def uninstall_sleep_wake(self) -> dict:
        return await self._run_setup_mode("uninstall")

    def _ensure_action_lock(self) -> asyncio.Lock:
        if self._action_lock is None:
            self._action_lock = asyncio.Lock()
        return self._action_lock

    def _invalidate_cached_object(self) -> None:
        self._cached_object_path = None

    async def _perform_action(self, action: str) -> dict:
        lock = self._ensure_action_lock()
        async with lock:
            try:
                return await self._perform_action_once(action)
            except CecError as exc:
                if self._is_stale_object_error(exc):
                    self._invalidate_cached_object()
                    try:
                        return await self._perform_action_once(action)
                    except CecError as retry_exc:
                        return {
                            "ok": False,
                            "action": action,
                            "error": str(retry_exc),
                        }
                return {
                    "ok": False,
                    "action": action,
                    "error": str(exc),
                }

    async def _perform_action_once(self, action: str) -> dict:
        if action not in ACTION_METHODS:
            raise CecError("Unsupported action")

        session = self._get_session_context()
        object_path = await self._get_object_path(session)
        audio_logical_address = await self._read_audio_logical_address(
            session, object_path
        )
        await self._call_cec_method(
            session,
            object_path,
            ACTION_METHODS[action],
            audio_logical_address,
        )
        return {
            "ok": True,
            "action": action,
            "audioLogicalAddress": audio_logical_address,
            "objectPath": object_path,
        }

    async def _load_status(self) -> dict:
        session = self._get_session_context()
        try:
            object_path = await self._get_object_path(session)
            active = await self._read_active(session, object_path)
            audio_logical_address = await self._read_audio_logical_address(
                session, object_path
            )
        except CecError as exc:
            if self._is_stale_object_error(exc):
                self._invalidate_cached_object()
                object_path = await self._get_object_path(session)
                active = await self._read_active(session, object_path)
                audio_logical_address = await self._read_audio_logical_address(
                    session, object_path
                )
            else:
                raise

        warning = None
        if not active:
            warning = "CEC device detected but not currently active"

        return {
            "active": active,
            "audioLogicalAddress": audio_logical_address,
            "objectPath": object_path,
            "warning": warning,
        }

    def _get_session_context(self) -> SessionContext:
        busctl_path = shutil.which("busctl")
        if not busctl_path:
            raise CecError("busctl not found")

        username = os.environ.get("DECKY_USER") or getattr(decky, "DECKY_USER", None)
        if username:
            try:
                user_entry = pwd.getpwnam(username)
            except KeyError as exc:
                raise CecError("Decky user could not be resolved") from exc
        else:
            current_uid = os.getuid()
            if current_uid == 0:
                raise CecError("Decky user could not be resolved")
            user_entry = pwd.getpwuid(current_uid)
            username = user_entry.pw_name

        runtime_dir = f"/run/user/{user_entry.pw_uid}"
        bus_path = os.path.join(runtime_dir, "bus")
        if not os.path.exists(bus_path):
            raise CecError("User session bus unavailable")

        env = self._sanitized_env()
        env.update(
            {
                "HOME": user_entry.pw_dir,
                "LOGNAME": username,
                "USER": username,
                "XDG_RUNTIME_DIR": runtime_dir,
                "DBUS_SESSION_BUS_ADDRESS": f"unix:path={bus_path}",
                "LC_ALL": "C",
            }
        )

        return SessionContext(
            username=username,
            busctl_path=busctl_path,
            systemctl_path=shutil.which("systemctl"),
            runuser_path=shutil.which("runuser"),
            env_path=shutil.which("env"),
            env=env,
        )

    async def _get_object_path(self, session: SessionContext) -> str:
        if self._cached_object_path:
            return self._cached_object_path

        device = await self._discover_device(session)
        self._cached_object_path = device.object_path
        return device.object_path

    async def _discover_device(
        self,
        session: SessionContext,
        *,
        allow_start_service: bool = True,
    ) -> DeviceInfo:
        try:
            return await self._discover_device_once(session)
        except CecError as exc:
            if not allow_start_service:
                raise
            if session.systemctl_path is None:
                raise exc

            decky.logger.info("Attempting to start cecd.service for discovery")
            try:
                await self._run_session_command(
                    session,
                    (
                        session.systemctl_path,
                        "--user",
                        "start",
                        "cecd.service",
                    ),
                    error_message="cecd service unavailable",
                )
            except CecError as start_exc:
                decky.logger.warning("Failed to start cecd.service: %s", start_exc)
                raise exc

            deadline = time.monotonic() + DISCOVERY_POLL_SECONDS
            last_error = exc
            while time.monotonic() < deadline:
                await asyncio.sleep(DISCOVERY_POLL_INTERVAL_SECONDS)
                try:
                    return await self._discover_device_once(session)
                except CecError as poll_exc:
                    last_error = poll_exc

            raise last_error

    async def _discover_device_once(self, session: SessionContext) -> DeviceInfo:
        inactive_device: DeviceInfo | None = None
        saw_service_unavailable = False

        for object_path in CEC_OBJECT_PATHS:
            try:
                await self._read_property(
                    session,
                    object_path,
                    "AudioLogicalAddress",
                    error_message="No CEC device object discovered",
                )
                active = await self._read_active(session, object_path)
            except CecError as exc:
                if self._is_service_unavailable_error(exc):
                    saw_service_unavailable = True
                continue

            device = DeviceInfo(object_path=object_path, active=active)
            if active:
                return device
            if inactive_device is None:
                inactive_device = device

        if inactive_device is not None:
            return inactive_device
        if saw_service_unavailable:
            raise CecError("cecd service unavailable")
        raise CecError("No CEC device object discovered")

    async def _read_active(self, session: SessionContext, object_path: str) -> bool:
        stdout = await self._read_property(
            session,
            object_path,
            "Active",
            error_message="No CEC device object discovered",
        )
        return self._parse_bool_property(stdout)

    async def _read_audio_logical_address(
        self,
        session: SessionContext,
        object_path: str,
    ) -> int:
        stdout = await self._read_property(
            session,
            object_path,
            "AudioLogicalAddress",
            error_message="No CEC device object discovered",
        )
        return self._parse_audio_logical_address(stdout)

    async def _read_property(
        self,
        session: SessionContext,
        object_path: str,
        property_name: str,
        *,
        error_message: str,
    ) -> str:
        result = await self._run_session_command(
            session,
            (
                session.busctl_path,
                "--user",
                "get-property",
                SERVICE_NAME,
                object_path,
                DEVICE_INTERFACE,
                property_name,
            ),
            error_message=error_message,
        )
        return result.stdout

    async def _call_cec_method(
        self,
        session: SessionContext,
        object_path: str,
        method_name: str,
        audio_logical_address: int,
    ) -> None:
        await self._run_session_command(
            session,
            (
                session.busctl_path,
                "--user",
                "call",
                SERVICE_NAME,
                object_path,
                DEVICE_INTERFACE,
                method_name,
                "y",
                str(audio_logical_address),
            ),
            error_message="D-Bus method failure or CEC transmission error",
        )

    async def _run_setup_mode(self, mode: str) -> dict[str, Any]:
        lock = self._ensure_action_lock()
        async with lock:
            script_path = self._get_setup_script_path()
            args = (self._get_bash_path(), script_path, f"--{mode}")
            if mode == "install":
                args = (*args, "--yes")

            result = await self._run_command(
                args,
                env=self._setup_env(),
                error_message=f"Setup script failed during {mode}",
                timeout_seconds=SETUP_COMMAND_TIMEOUT_SECONDS,
                check_returncode=False,
            )
            return self._parse_setup_result(mode, result)

    async def _run_session_command(
        self,
        session: SessionContext,
        args: tuple[str, ...],
        *,
        error_message: str,
    ) -> CommandResult:
        effective_args = args
        effective_env = session.env

        if os.geteuid() == 0 and session.username != "root":
            if not session.runuser_path or not session.env_path:
                raise CecError("runuser not found")
            env_pairs = tuple(f"{key}={value}" for key, value in session.env.items())
            effective_args = (
                session.runuser_path,
                "-u",
                session.username,
                "--",
                session.env_path,
                *env_pairs,
                *args,
            )
            effective_env = self._sanitized_env()
            effective_env.setdefault("LC_ALL", "C")

        return await self._run_command(
            effective_args,
            env=effective_env,
            error_message=error_message,
        )

    def _get_bash_path(self) -> str:
        bash_path = shutil.which("bash")
        if not bash_path:
            raise CecError("bash not found")
        return bash_path

    def _get_setup_script_path(self) -> str:
        plugin_dir = getattr(decky, "DECKY_PLUGIN_DIR", None)
        if not plugin_dir:
            raise CecError("Decky plugin directory unavailable")
        script_path = os.path.join(plugin_dir, SETUP_SCRIPT_RELATIVE_PATH)
        if not os.path.exists(script_path):
            raise CecError("Bundled sleep/wake setup script is missing")
        return script_path

    def _setup_env(self) -> dict[str, str]:
        env = self._sanitized_env()
        env.setdefault("LC_ALL", "C")
        return env

    @staticmethod
    def _sanitized_env() -> dict[str, str]:
        env = os.environ.copy()
        for key in (
            "LD_LIBRARY_PATH",
            "PYTHONHOME",
            "PYTHONPATH",
            "PYTHONEXECUTABLE",
            "PYINSTALLER_SAFE_PATH",
            "PYINSTALLER_RESET_ENVIRONMENT",
            "_MEIPASS2",
            "_PYI_APPLICATION_HOME_DIR",
            "_PYI_ARCHIVE_FILE",
            "_PYI_PARENT_PROCESS_LEVEL",
            "_PYI_LINUX_PROCESS_NAME",
        ):
            env.pop(key, None)
        return env

    def _parse_setup_result(self, mode: str, result: CommandResult) -> dict[str, Any]:
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        combined_output = "\n".join(
            part for part in (stdout, stderr) if part
        ).strip()
        lines = [line.strip() for line in combined_output.splitlines() if line.strip()]

        ok_lines = [line for line in lines if line.startswith("OK")]
        warn_lines = [line for line in lines if line.startswith("WARN") or line.startswith("Warning:")]
        fail_lines = [line for line in lines if line.startswith("FAIL") or line.startswith("Error:")]

        state = self._classify_setup_state(result.returncode, ok_lines, fail_lines, warn_lines)
        summary = self._summarize_setup_state(mode, state, result.returncode, lines)

        details = {
            "stateFile": self._extract_value(lines, "State file loaded:"),
            "cecPhysicalAddress": self._extract_value(lines, "Stored CEC physical address:"),
            "cecDevice": self._extract_value(lines, "Stored CEC device:"),
            "cecObjectPath": self._extract_value(lines, "CEC D-Bus path:"),
            "bluetoothTarget": self._extract_value(
                lines, "Configured Bluetooth wake target:"
            )
            or self._extract_value(lines, "Stored Bluetooth wake target:"),
            "bluetoothHelper": self._extract_value(lines, "bt-wakeup helper path:"),
            "keepListPath": self._extract_value(lines, "Atomic-update keep-list exists:"),
            "persistentLayout": self._extract_value(lines, "Persistent layout:"),
        }

        return {
            "ok": result.returncode == 0,
            "action": mode,
            "state": state,
            "summary": summary,
            "warnings": warn_lines,
            "failures": fail_lines,
            "details": details,
            "stdout": stdout,
            "stderr": stderr,
            "returncode": result.returncode,
        }

    def _classify_setup_state(
        self,
        returncode: int,
        ok_lines: list[str],
        fail_lines: list[str],
        warn_lines: list[str],
    ) -> str:
        if returncode == 0:
            return "configured"

        combined = "\n".join([*ok_lines, *warn_lines, *fail_lines])
        if "Recoverable partial-install damage" in combined:
            return "needs_repair"
        if "State file loaded:" in combined:
            return "needs_repair"
        if any(
            token in combined
            for token in (
                "not enabled",
                "wakeup is",
                "Could not read",
                "does not match",
                "legacy state path",
            )
        ):
            return "needs_repair"
        if any(
            token in combined
            for token in (
                "Persistent data directory missing",
                "CEC helper missing",
                "Bluetooth helper missing",
                "Atomic-update keep-list missing",
                "State file missing",
                "unit missing",
            )
        ) and not ok_lines:
            return "needs_setup"
        if fail_lines:
            return "needs_repair"
        return "error"

    def _summarize_setup_state(
        self,
        mode: str,
        state: str,
        returncode: int,
        lines: list[str],
    ) -> str:
        if mode == "install":
            if returncode == 0:
                return "Sleep/wake setup is installed and verified."
            return "Sleep/wake setup install reported problems."
        if mode == "uninstall":
            if returncode == 0:
                return "Sleep/wake setup removed."
            return "Sleep/wake setup uninstall reported problems."
        if state == "configured":
            return "Sleep/wake setup is installed and healthy."
        if state == "needs_setup":
            return "Sleep/wake setup is not installed yet."
        if state == "needs_repair":
            return "Sleep/wake setup exists but needs repair."
        if lines:
            return lines[-1]
        return "Sleep/wake setup status is unavailable."

    @staticmethod
    def _extract_value(lines: list[str], prefix: str) -> str | None:
        for line in lines:
            if prefix in line:
                return line.split(prefix, 1)[1].strip()
        return None

    async def _run_command(
        self,
        args: tuple[str, ...],
        *,
        env: dict[str, str],
        error_message: str,
        timeout_seconds: float = COMMAND_TIMEOUT_SECONDS,
        check_returncode: bool = True,
    ) -> CommandResult:
        process = await asyncio.create_subprocess_exec(
            *args,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            decky.logger.error("Command timed out: %s", list(args))
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
            timeout_message = "D-Bus call timeout" if timeout_seconds == COMMAND_TIMEOUT_SECONDS else "Command timeout"
            raise CecError(timeout_message) from exc

        stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()

        if process.returncode != 0 and check_returncode:
            decky.logger.error(
                "Command failed rc=%s args=%s stdout=%r stderr=%r",
                process.returncode,
                list(args),
                stdout,
                stderr,
            )
            if self._is_bus_unavailable_error_text(stderr):
                raise CecError(
                    "User session bus unavailable",
                    stderr=stderr,
                    returncode=process.returncode,
                )
            raise CecError(
                error_message,
                stderr=stderr,
                returncode=process.returncode,
            )

        if stderr:
            decky.logger.info("Command stderr args=%s stderr=%r", list(args), stderr)

        return CommandResult(
            args=args,
            returncode=process.returncode,
            stdout=stdout,
            stderr=stderr,
        )

    @staticmethod
    def _parse_bool_property(stdout: str) -> bool:
        parts = stdout.split()
        if len(parts) != 2 or parts[0] != "b" or parts[1] not in {"true", "false"}:
            raise CecError("Malformed Active property")
        return parts[1] == "true"

    @staticmethod
    def _parse_audio_logical_address(stdout: str) -> int:
        parts = stdout.split()
        if len(parts) != 2 or parts[0] != "y":
            raise CecError("Malformed AudioLogicalAddress")

        try:
            value = int(parts[1], 10)
        except ValueError as exc:
            raise CecError("Malformed AudioLogicalAddress") from exc

        if value < 0 or value > 14:
            raise CecError("Invalid audio logical address")

        return value

    @staticmethod
    def _target_label(audio_logical_address: int) -> str:
        if audio_logical_address == 0:
            return "TV"
        if audio_logical_address == 5:
            return "Audio system"
        return f"Logical address {audio_logical_address}"

    @staticmethod
    def _is_stale_object_error(exc: CecError) -> bool:
        text = exc.stderr or str(exc)
        return any(
            token in text
            for token in (
                "Unknown object",
                "UnknownObject",
                "No such object path",
            )
        )

    @staticmethod
    def _is_service_unavailable_error(exc: CecError) -> bool:
        return Plugin._is_service_unavailable_error_text(exc.stderr or str(exc))

    @staticmethod
    def _is_service_unavailable_error_text(text: str) -> bool:
        return any(
            token in text
            for token in (
                "The name is not activatable",
                "The name is not provided by any .service files",
                "Unknown service",
                "ServiceUnknown",
                "No such service",
            )
        )

    @staticmethod
    def _is_bus_unavailable_error_text(text: str) -> bool:
        return any(
            token in text
            for token in (
                "Failed to connect to bus",
                "No such file or directory",
                "Connection refused",
            )
        )
