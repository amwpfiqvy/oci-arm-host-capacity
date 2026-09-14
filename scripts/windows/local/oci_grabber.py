#!/usr/bin/env python3
"""本机直接调用 OCI API 抢 ARM 实例（GitHub Actions 版的本机替代）。

逻辑移植自 index.php / src/*.php：
  列实例 -> 数 RUNNING 的 A1 -> 达到上限退出 -> 分配 prefixN 名字
  -> 列可用域 -> 逐域 POST 创建 -> Out of host capacity 则换下一个域。
成功或已达上限后自动禁用本机计划任务，并发 Server 酱通知（可选）。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from email.utils import formatdate
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
LOG_DIR = ROOT / "logs"
LOG_KEEP_DAYS = 7
TASK_NAME = "OCI-ARM-Grabber"
SHAPE = "VM.Standard.A1.Flex"
API = "20160918"

REQUIRED = (
    "OCI_REGION",
    "OCI_USER_ID",
    "OCI_TENANCY_ID",
    "OCI_KEY_FINGERPRINT",
    "OCI_PRIVATE_KEY_FILENAME",
    "OCI_SUBNET_ID",
    "OCI_IMAGE_ID",
    "OCI_SSH_PUBLIC_KEY",
)


class GrabberError(RuntimeError):
    """可直接反馈给定时任务用户的错误。"""


def write_log(message: str) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        today = datetime.now().date()
        for path in LOG_DIR.glob("*.log"):
            try:
                if today - datetime.fromtimestamp(
                    path.stat().st_mtime
                ).date() >= timedelta(days=LOG_KEEP_DAYS):
                    path.unlink()
            except OSError:
                continue
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        with (LOG_DIR / f"{today.isoformat()}.log").open(
            "a", encoding="utf-8"
        ) as stream:
            stream.write(f"{stamp} {message}\n")
    except OSError:
        pass


def load_env() -> dict[str, str]:
    if not ENV_FILE.is_file():
        raise GrabberError(
            f"缺少 {ENV_FILE.name}：复制 .env.example 为 .env 并填写"
        )
    env: dict[str, str] = {}
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    missing = [key for key in REQUIRED if not env.get(key)]
    if missing:
        raise GrabberError(f".env 缺少必填项：{', '.join(missing)}")
    return env


class OciSigner:
    """OCI Request Signing version 1（等价 src/Signer.php）。"""

    def __init__(self, tenancy: str, user: str, fingerprint: str, key_path: str):
        pem = Path(key_path)
        if not pem.is_file():
            raise GrabberError(f"无法读取私钥文件：{key_path}")
        try:
            self.key = serialization.load_pem_private_key(
                pem.read_bytes(), password=None
            )
        except (ValueError, TypeError) as exc:
            raise GrabberError(f"私钥解析失败（应为 PEM RSA）：{exc}") from exc
        self.key_id = f"{tenancy}/{user}/{fingerprint}"

    def signed_headers(
        self, method: str, url: str, body: bytes | None = None
    ) -> dict[str, str]:
        parts = urlsplit(url)
        date = formatdate(usegmt=True)
        target = parts.path or "/"
        if parts.query:
            target += f"?{parts.query}"
        values = {
            "date": date,
            "(request-target)": f"{method.lower()} {target}",
            "host": parts.netloc,
        }
        names = ["date", "(request-target)", "host"]
        if body is not None:
            values["content-length"] = str(len(body))
            values["content-type"] = "application/json"
            values["x-content-sha256"] = base64.b64encode(
                hashlib.sha256(body).digest()
            ).decode()
            names += ["content-length", "content-type", "x-content-sha256"]
        signing = "\n".join(f"{name}: {values[name]}" for name in names).encode()
        signature = self.key.sign(signing, padding.PKCS1v15(), hashes.SHA256())
        auth = (
            'Signature version="1"'
            f',keyId="{self.key_id}"'
            ',algorithm="rsa-sha256"'
            f',headers="{" ".join(names)}"'
            f',signature="{base64.b64encode(signature).decode()}"'
        )
        headers = {"Date": date, "Authorization": auth}
        for name in ("content-length", "content-type", "x-content-sha256"):
            if name in values:
                headers[name] = values[name]
        return headers


class OciClient:
    def __init__(self, env: dict[str, str]) -> None:
        region = env["OCI_REGION"]
        self.tenancy = env["OCI_TENANCY_ID"]
        self.iaas = f"https://iaas.{region}.oraclecloud.com"
        self.identity = f"https://identity.{region}.oraclecloud.com"
        self.signer = OciSigner(
            env["OCI_TENANCY_ID"],
            env["OCI_USER_ID"],
            env["OCI_KEY_FINGERPRINT"],
            env["OCI_PRIVATE_KEY_FILENAME"],
        )
        proxy = (
            os.environ.get("HTTPS_PROXY")
            or os.environ.get("https_proxy")
            or ""
        ).strip()
        handler = (
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
            if proxy
            else urllib.request.ProxyHandler({})
        )
        self.opener = urllib.request.build_opener(handler)

    def request(
        self, method: str, url: str, body: dict[str, Any] | None = None
    ) -> tuple[int, Any]:
        raw = None if body is None else json.dumps(body).encode("utf-8")
        headers = self.signer.signed_headers(method, url, raw)
        request = urllib.request.Request(url, data=raw, method=method, headers=headers)
        request.add_header("User-Agent", "oci-arm-grabber")
        try:
            with self.opener.open(request, timeout=60) as response:
                payload = response.read()
                status = response.status
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            status = exc.code
        except urllib.error.URLError as exc:
            raise GrabberError(f"无法访问 OCI API（{exc.reason}）") from exc
        text = payload.decode("utf-8", errors="replace")
        if not text:
            return status, None
        try:
            return status, json.loads(text)
        except ValueError:
            return status, text[:500]

    def get_instances(self) -> list[dict[str, Any]]:
        query = f"compartmentId={quote(self.tenancy, safe='')}"
        status, data = self.request(
            "GET", f"{self.iaas}/{API}/instances?{query}"
        )
        if status != 200 or not isinstance(data, list):
            raise GrabberError(f"读取实例列表失败 HTTP {status}：{data}")
        return data

    def get_availability_domains(self) -> list[str]:
        query = f"compartmentId={quote(self.tenancy, safe='')}"
        status, data = self.request(
            "GET", f"{self.identity}/{API}/availabilityDomains?{query}"
        )
        if status != 200 or not isinstance(data, list):
            raise GrabberError(f"读取可用域失败 HTTP {status}：{data}")
        return [str(item["name"]) for item in data]

    def create_instance(
        self,
        env: dict[str, str],
        ad: str,
        name: str | None,
    ) -> tuple[int, Any]:
        display_name = name or "oci-arm-" + datetime.now().strftime(
            "%Y-%m-%d-%H-%M-%S"
        )
        vnic: dict[str, Any] = {
            "subnetId": env["OCI_SUBNET_ID"],
            "assignPublicIp": True,
        }
        if name:
            vnic["hostnameLabel"] = name
        source: dict[str, Any] = {
            "sourceType": "image",
            "imageId": env["OCI_IMAGE_ID"],
        }
        if env.get("OCI_BOOT_VOLUME_SIZE_IN_GBS"):
            source["bootVolumeSizeInGBs"] = int(env["OCI_BOOT_VOLUME_SIZE_IN_GBS"])
        payload = {
            "compartmentId": self.tenancy,
            "availabilityDomain": ad,
            "shape": SHAPE,
            "displayName": display_name,
            "sourceDetails": source,
            "createVnicDetails": vnic,
            "metadata": {"ssh_authorized_keys": env["OCI_SSH_PUBLIC_KEY"]},
            "shapeConfig": {
                "ocpus": int(env.get("OCI_OCPUS") or 2),
                "memoryInGBs": int(env.get("OCI_MEMORY_IN_GBS") or 12),
            },
        }
        return self.request("POST", f"{self.iaas}/{API}/instances", payload)


def pick_instance_name(
    instances: list[dict[str, Any]], prefix: str
) -> str | None:
    if not prefix:
        return None
    used: set[str] = set()
    singles = {str(number) for number in range(1, 10)}
    for instance in instances:
        if instance.get("lifecycleState") == "TERMINATED":
            continue
        for field in ("displayName",):
            value = str(instance.get(field) or "").lower()
            if value.startswith(prefix) and value[len(prefix):] in singles:
                used.add(value)
        vnic = instance.get("createVnicDetails") or {}
        label = str(vnic.get("hostnameLabel") or "").lower()
        if label.startswith(prefix) and label[len(prefix):] in singles:
            used.add(label)
    for number in range(1, 10):
        candidate = f"{prefix}{number}"
        if candidate not in used:
            return candidate
    return None


def send_serverchan(key: str, title: str, desp: str) -> str:
    data = (
        f"title={quote(title)}&desp={quote(desp)}"
    ).encode("utf-8")
    request = urllib.request.Request(
        f"https://sctapi.ftqq.com/{key}.send", data=data, method="POST"
    )
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response.read()
            return "Server酱通知已发送"
    except Exception as exc:  # 通知失败不影响抢机结果
        return f"Server酱通知失败（{exc}）"


def disable_scheduled_task() -> str:
    try:
        completed = subprocess.run(
            ["schtasks.exe", "/Change", "/TN", TASK_NAME, "/DISABLE"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"禁用计划任务失败（{exc}），请手动禁用 {TASK_NAME}"
    if completed.returncode == 0:
        return f"已自动禁用计划任务 {TASK_NAME}（成功即停，防止空转）"
    return (
        f"禁用计划任务失败（{(completed.stderr or completed.stdout).strip()}），"
        f"请手动禁用 {TASK_NAME}"
    )


def run(dry_run: bool) -> str:
    env = load_env()
    max_instances = int(env.get("OCI_MAX_INSTANCES") or 2)
    prefix = (env.get("OCI_INSTANCE_NAME_PREFIX") or "").strip().lower()
    client = OciClient(env)

    instances = client.get_instances()
    running = [
        item
        for item in instances
        if item.get("shape") == SHAPE and item.get("lifecycleState") == "RUNNING"
    ]
    lines = [
        f"Region: {env['OCI_REGION']}  Shape: {SHAPE}",
        f"Running A1: {len(running)}/{max_instances}  Prefix: {prefix or '(date-based)'}",
    ]
    if len(running) >= max_instances:
        message = "Already have " f"{len(running)} instance(s) running, max {max_instances}"
        return "\n".join(lines + [message, disable_scheduled_task()])

    name = pick_instance_name(instances, prefix)
    if prefix and name is None:
        raise GrabberError(f"{prefix}1-{prefix}9 名字已用尽，无法分配新实例名")
    lines.append(f"Next instance name: {name or 'date-based default'}")

    domains = [env["OCI_AVAILABILITY_DOMAIN"]] if env.get("OCI_AVAILABILITY_DOMAIN") else client.get_availability_domains()
    if not domains:
        raise GrabberError("没有可用域")
    lines.append(f"Availability domains: {', '.join(domains)}")

    if dry_run:
        return "\n".join(lines + ["[dry-run] 到此为止，未提交创建请求"])

    for ad in domains:
        status, data = client.create_instance(env, ad, name)
        if status == 200:
            extra = []
            key = (env.get("SERVERCHAN_SENDKEY") or "").strip()
            if key:
                extra.append(
                    send_serverchan(
                        key,
                        "OCI ARM 抢机成功",
                        f"实例 {name or '(default)'} 已创建于 {ad}\n"
                        f"Region: {env['OCI_REGION']}\n"
                        f"{json.dumps(data, ensure_ascii=False)[:800]}",
                    )
                )
            extra.append(disable_scheduled_task())
            return "\n".join(
                lines
                + [f"SUCCESS in {ad}:", json.dumps(data, ensure_ascii=False, indent=2)]
                + extra
            )
        message = data.get("message") if isinstance(data, dict) else str(data)
        lines.append(f"Failed in {ad} HTTP {status}: {message}")
        # Capacity and rate-limit are expected; retry next interval.
        if status == 429 or (message and "Out of host capacity" in str(message)):
            time.sleep(2)
            continue
        raise GrabberError(f"创建实例失败 HTTP {status}：{message}")

    return "\n".join(lines + ["Out of capacity in all availability domains, retry later"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="只列实例与配置，不提交创建"
    )
    args = parser.parse_args()
    code = 0
    try:
        result = run(args.dry_run)
    except GrabberError as exc:
        code, result = 1, f"{exc}。"
    except Exception as exc:
        code, result = 1, f"脚本异常（{exc}），需要人工处理。"
    write_log(result.replace("\n", " | "))
    print(result, flush=True)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
