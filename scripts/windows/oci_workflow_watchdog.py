#!/usr/bin/env python3
"""通过本机 Chrome CDP 监控并按需手动触发 OCI ARM 工作流。"""
from __future__ import annotations

import argparse
import asyncio
import json
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import websockets


DEFAULT_CDP = "http://127.0.0.1:9222"
TARGET_URL = (
    "https://github.com/amwpfiqvy/oci-arm-host-capacity/"
    "actions/workflows/oci-arm-capacity.yml"
)
THRESHOLD_SECONDS = 420
LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_KEEP_DAYS = 7

# profile 锁定：GitHub 登录态只在 amwpfiqvy@gmail.com（GitHub 用户名 amwpfiqvy）
# 登录的 Chrome 窗口。操作目标标签前校验其 profile 归属（github.com 标签的
# meta[name=user-login]），防止跑到其它账号的浏览器窗口。与
# 自动领取agentrouter每日额度/agentrouter_relogin.py 同一套判据。
GITHUB_USERNAME = "amwpfiqvy"


class WatchdogError(RuntimeError):
    """可直接反馈给定时任务用户的错误。"""


def get_targets(cdp_url: str) -> list[dict[str, Any]]:
    with urllib.request.urlopen(cdp_url + "/json", timeout=5) as response:
        return json.load(response)


def page_targets(targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [target for target in targets if target.get("type") == "page"]


def normalized_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def is_target(target: dict[str, Any]) -> bool:
    return normalized_url(target.get("url", "")) == TARGET_URL


def write_log(message: str) -> None:
    """记录固定日期日志；不把日志内容混入 stdout。"""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        today = datetime.now().date()
        for path in LOG_DIR.glob("*.log"):
            try:
                if today - datetime.fromtimestamp(
                    path.stat().st_mtime
                ).date() > timedelta(days=LOG_KEEP_DAYS):
                    path.unlink()
            except OSError:
                continue
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        with (LOG_DIR / f"{today.isoformat()}.log").open(
            "a", encoding="utf-8"
        ) as stream:
            stream.write(f"{stamp} {message}\n")
    except OSError:
        # 日志失败不能影响浏览器页面恢复和最终结果。
        pass


class Tab:
    def __init__(self, websocket_url: str):
        self.websocket_url = websocket_url
        self.socket: Any = None
        self.message_id = 0

    async def __aenter__(self) -> "Tab":
        self.socket = await websockets.connect(
            self.websocket_url,
            open_timeout=5,
            close_timeout=5,
            max_size=16 * 1024 * 1024,
        )
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self.socket is not None:
            await self.socket.close()

    async def call(
        self, method: str, params: dict[str, Any] | None = None, timeout: float = 30
    ) -> dict[str, Any]:
        self.message_id += 1
        message_id = self.message_id
        await self.socket.send(
            json.dumps(
                {"id": message_id, "method": method, "params": params or {}}
            )
        )
        while True:
            message = json.loads(await asyncio.wait_for(self.socket.recv(), timeout))
            if message.get("id") == message_id:
                return message

    async def evaluate(self, expression: str) -> Any:
        response = await self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
        )
        result = response.get("result", {})
        if "exceptionDetails" in result:
            raise WatchdogError("页面脚本执行失败")
        remote = result.get("result", {})
        if remote.get("subtype") == "error":
            raise WatchdogError(remote.get("description", "页面脚本执行失败"))
        return remote.get("value")

    async def real_click(self, rect: dict[str, float]) -> None:
        x = rect["x"] + rect["w"] / 2
        y = rect["y"] + rect["h"] / 2
        for event_type in ("mousePressed", "mouseReleased"):
            await self.call(
                "Input.dispatchMouseEvent",
                {
                    "type": event_type,
                    "x": x,
                    "y": y,
                    "button": "left",
                    "clickCount": 1,
                },
            )

    async def reload_and_wait(self, timeout: float = 15) -> None:
        await self.call("Page.reload", {"ignoreCache": False})
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            try:
                state = await self.evaluate("document.readyState")
                if state == "complete":
                    await asyncio.sleep(0.5)
                    return
            except Exception:
                pass
            await asyncio.sleep(0.5)
        raise WatchdogError("刷新工作流页面后超过15秒仍未完成加载")


PAGE_STATE_JS = r"""
(()=>{
  const visible=e=>{
    const r=e.getBoundingClientRect(), s=getComputedStyle(e);
    return r.width>0 && r.height>0 && s.display!=='none' &&
      s.visibility!=='hidden' && s.opacity!=='0';
  };
  const clean=e=>(e?.innerText||e?.textContent||'').replace(/\s+/g,' ').trim();
  const runs=[...document.querySelectorAll('relative-time,time')].map(time=>{
    const direct=time.closest('a[href*="/actions/runs/"]');
    const row=time.closest('li,[role="row"],.Box-row');
    const link=direct || row?.querySelector('a[href*="/actions/runs/"]');
    return link ? {
      datetime:time.getAttribute('datetime'),
      text:clean(link.closest('li,[role="row"],.Box-row')||link),
      href:link.getAttribute('href')
    } : null;
  }).filter(x=>x && x.datetime && x.href);
  const controls=[...document.querySelectorAll(
    'button,summary,a,[role="button"]'
  )].filter(visible).map(e=>{
    const r=e.getBoundingClientRect();
    return {
      text:clean(e),
      aria:e.getAttribute('aria-label')||'',
      tag:e.tagName,
      type:e.getAttribute('type')||'',
      cls:String(e.className||''),
      rect:{x:r.x,y:r.y,w:r.width,h:r.height}
    };
  });
  return {
    url:location.href,
    title:document.title,
    text:(document.body?.innerText||'').slice(0,8000),
    runs,
    controls
  };
})()
"""



# 找到菜单内确认按钮后直接合成 click()（真实坐标点击对 GitHub 该按钮同样
# 无效，2026-09-11 实测：点击后菜单不关、不触发运行）。
CONFIRM_AND_CLICK_JS = r"""
(()=>{ 
  const visible=e=>{
    const r=e.getBoundingClientRect(), s=getComputedStyle(e);
    return r.width>0 && r.height>0 && s.display!=='none' &&
      s.visibility!=='hidden' && s.opacity!=='0';
  };
  const clean=e=>(e?.innerText||e?.textContent||'').replace(/\s+/g,' ').trim();
  const in_menu=e=>!!e.closest(
    'details-menu,[role="menu"],[role="dialog"],dialog,.SelectMenu-modal,.Popover'
  );
  const all=[...document.querySelectorAll('button,[role="button"]')]
    .filter(e=>visible(e) && clean(e).toLowerCase()==='run workflow');
  const e=all.find(x=>in_menu(x) || /btn-primary|primary/.test(String(x.className)))
    || all[1];
  if(!e)return false;
  e.click();
  return true;
})()
"""

# 打开 Run workflow 菜单。GitHub 页面的 summary 对 CDP 真实坐标点击不响应
# （2026-09-11 实测：真实点击 openDetails=0，JS click() 立即打开），
# 必须用合成 click()——注意这与 Agent Router 的 semi-design 菜单行为相反。
OPEN_RUN_MENU_JS = r"""
(()=>{ 
  const visible=e=>{
    const r=e.getBoundingClientRect(), s=getComputedStyle(e);
    return r.width>0 && r.height>0 && s.display!=='none' &&
      s.visibility!=='hidden' && s.opacity!=='0';
  };
  const clean=e=>(e?.innerText||e?.textContent||'').replace(/\s+/g,' ').trim();
  const e=[...document.querySelectorAll('button,summary,a,[role="button"]')]
    .find(x=>visible(x) && clean(x).toLowerCase().includes('run workflow'));
  if(!e)return false;
  e.click();
  return true;
})()
"""

# 重试前强制收起所有展开的 details，避免 click toggle 语义把菜单关掉。
CLOSE_MENUS_JS = r"""
(()=>{ 
  document.querySelectorAll('details[open]').forEach(d=>{d.open=false});
  return true;
})()
"""

# 菜单未打开时的诊断快照：details 开闭状态 + 可见控件文本，写入错误消息与日志。
MENU_DIAG_JS = r"""
(()=>{ 
  const clean=e=>(e?.innerText||e?.textContent||'').replace(/\s+/g,' ').trim();
  const open=[...document.querySelectorAll('details[open]')]
    .map(d=>clean(d.querySelector('summary')).slice(0,30));
  const buttons=[...document.querySelectorAll('button,[role="button"],summary')]
    .filter(e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0})
    .map(e=>clean(e).slice(0,30)).filter(Boolean).slice(0,15);
  return {open, buttons};
})()
"""


def latest_run(state: dict[str, Any]) -> dict[str, Any] | None:
    runs = []
    for run in state.get("runs") or []:
        try:
            value = datetime.fromisoformat(
                str(run["datetime"]).replace("Z", "+00:00")
            ).astimezone(timezone.utc)
            runs.append((value, run))
        except (KeyError, TypeError, ValueError):
            continue
    return max(runs, key=lambda item: item[0])[1] if runs else None


def validate_workflow_page(state: dict[str, Any]) -> None:
    url = state.get("url", "")
    text = state.get("text", "")
    lower_text = text.lower()
    if normalized_url(url) != TARGET_URL:
        raise WatchdogError("当前标签不是目标工作流页面")
    if "/login" in url or "sign in to github" in lower_text:
        raise WatchdogError("页面显示 GitHub 登录页，需要人工登录")
    if "oci arm host capacity checker" not in lower_text:
        raise WatchdogError("无法确认当前页面属于 OCI ARM 工作流")
    if not state.get("runs"):
        raise WatchdogError("无法从工作流运行列表读取可靠的执行记录")


USER_LOGIN_JS = (
    "document.querySelector('meta[name=user-login]')?.content || ''"
)


async def locate_profile_pages(cdp_url: str) -> list[dict[str, Any]]:
    """定位 amwpfiqvy 登录的 Chrome 窗口（browserContext），返回该窗口内全部 page 标签。

    判据：窗口内有 github.com 标签且其 meta[user-login]==amwpfiqvy。
    后续查找/新开标签都只在该窗口内进行；找不到验证过的窗口时抛错，
    绝不在其它账号的窗口里操作。
    """
    pages = page_targets(get_targets(cdp_url))
    with urllib.request.urlopen(cdp_url + "/json/version", timeout=5) as response:
        browser_ws = json.load(response).get("webSocketDebuggerUrl")
    if not browser_ws:
        raise WatchdogError("无法获取 Chrome browser 级 CDP 地址")

    async with websockets.connect(
        browser_ws, open_timeout=5, close_timeout=5, max_size=16 * 1024 * 1024
    ) as socket:
        message_id = 0

        async def call(method: str, params: dict[str, Any] | None = None):
            nonlocal message_id
            message_id += 1
            await socket.send(
                json.dumps({"id": message_id, "method": method, "params": params or {}})
            )
            while True:
                message = json.loads(await asyncio.wait_for(socket.recv(), 15))
                if message.get("id") == message_id:
                    return message

        infos = await call("Target.getTargets")
    context_of = {}
    for info in infos.get("result", {}).get("targetInfos") or []:
        if info.get("type") == "page":
            context_of[info.get("targetId")] = info.get("browserContextId")

    # 用 github.com 标签的登录身份锁定窗口；命中后返回同窗口全部标签。
    for page in pages:
        if not page.get("url", "").startswith("https://github.com/"):
            continue
        websocket_url = page.get("webSocketDebuggerUrl")
        bcid = context_of.get(page["id"])
        if not websocket_url or not bcid:
            continue
        try:
            async with Tab(websocket_url) as tab:
                login = str(await tab.evaluate(USER_LOGIN_JS) or "").strip()
        except Exception:
            continue
        if login.lower() == GITHUB_USERNAME:
            return [p for p in pages if context_of.get(p["id"]) == bcid]
    raise WatchdogError(
        f"未找到 {GITHUB_USERNAME} 登录的 Chrome 窗口"
        "（需要有该账号登录的 github.com 标签），未执行任何操作"
    )


async def open_tab_via_anchor(
    anchor: dict[str, Any], url: str, cdp_url: str
) -> dict[str, Any] | None:
    """在锚点标签注入一次性 click 监听（capture+阻断冒泡），用 CDP 真实
    点击使 window.open 带用户激活，新标签与锚点同 profile。
    实测（2026-09-11，Chrome 151）：纯合成 window.open 被弹窗拦截；
    Target.createTarget 带 browserContextId 不稳定（同 id 时成时败）；
    本方案在 github.com 锚点上验证可用。"""
    ws_url = anchor.get("webSocketDebuggerUrl")
    if not ws_url:
        return None
    before_ids = {t["id"] for t in page_targets(get_targets(cdp_url))}
    async with Tab(ws_url) as tab:
        center = await tab.evaluate(
            f"""(()=>{{
document.addEventListener('click', e => {{
  e.stopPropagation(); e.preventDefault();
  window.open({json.dumps(url)}, '_blank');
}}, {{once:true, capture:true}});
return {{x:Math.round(window.innerWidth/2), y:Math.round(window.innerHeight/2)}};
}})()"""
        )
        if not center:
            return None
        for event_type in ("mousePressed", "mouseReleased"):
            await tab.call(
                "Input.dispatchMouseEvent",
                {
                    "type": event_type,
                    "x": center["x"],
                    "y": center["y"],
                    "button": "left",
                    "clickCount": 1,
                },
            )
    for _ in range(10):
        await asyncio.sleep(0.5)
        for target in page_targets(get_targets(cdp_url)):
            if (
                target["id"] not in before_ids
                and "oci-arm-host-capacity" in target.get("url", "")
            ):
                return target
    return None


async def open_tab_via_context(
    anchor: dict[str, Any], url: str, cdp_url: str
) -> dict[str, Any] | None:
    """兜底：browser 级 Target.createTarget 带锚点 browserContextId。
    不稳定（同 id 时成时败，2026-09-11 实测），仅在锚点 window.open
    全部被弹窗拦截时使用。"""
    with urllib.request.urlopen(cdp_url + "/json/version", timeout=5) as response:
        browser_ws = json.load(response).get("webSocketDebuggerUrl")
    if not browser_ws:
        return None
    new_id = None
    async with websockets.connect(
        browser_ws, open_timeout=5, close_timeout=5, max_size=16 * 1024 * 1024
    ) as socket:
        message_id = 0

        async def call(method: str, params: dict[str, Any] | None = None):
            nonlocal message_id
            message_id += 1
            await socket.send(
                json.dumps(
                    {"id": message_id, "method": method, "params": params or {}}
                )
            )
            while True:
                message = json.loads(await asyncio.wait_for(socket.recv(), 15))
                if message.get("id") == message_id:
                    return message

        info = await call("Target.getTargetInfo", {"targetId": anchor["id"]})
        context_id = (
            info.get("result", {}).get("targetInfo", {}).get("browserContextId")
        )
        if not context_id:
            return None
        created = await call(
            "Target.createTarget", {"url": url, "browserContextId": context_id}
        )
        new_id = created.get("result", {}).get("targetId")
    if not new_id:
        return None
    for _ in range(10):
        await asyncio.sleep(0.5)
        for target in page_targets(get_targets(cdp_url)):
            if target["id"] == new_id and target.get("webSocketDebuggerUrl"):
                return target
    return None


async def open_target_if_needed(
    targets: list[dict[str, Any]], cdp_url: str
) -> list[dict[str, Any]]:
    # 先锁定 amwpfiqvy 窗口，查找/新开标签都只在该窗口内。
    profile_pages = await locate_profile_pages(cdp_url)
    candidates = [target for target in profile_pages if is_target(target)]
    if candidates:
        return candidates
    anchors = [
        page
        for page in profile_pages
        if page.get("url", "").startswith("https://github.com/")
        and page.get("webSocketDebuggerUrl")
    ]
    if not anchors:
        raise WatchdogError("目标窗口内没有可用作锚点的 github.com 标签")
    opened = None
    # Chrome 每页面有弹窗预算，单一锚点反复 window.open 会被拦；
    # 逐个锚点尝试，全被拦再用 createTarget 兜底。
    for anchor in anchors[:3]:
        opened = await open_tab_via_anchor(anchor, TARGET_URL, cdp_url)
        if opened is not None:
            break
    if opened is None:
        opened = await open_tab_via_context(anchors[0], TARGET_URL, cdp_url)
    if opened is None:
        raise WatchdogError("未能在 GitHub 登录态窗口打开目标工作流页面")
    return [opened]


async def choose_target(
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    # 候选已限定在 amwpfiqvy 窗口内（open_target_if_needed 已锁定），
    # 这里只做页面内容校验；meta 再读一次作双保险（也用于等待新标签渲染）。
    for candidate in candidates:
        websocket_url = candidate.get("webSocketDebuggerUrl")
        if not websocket_url:
            continue
        try:
            async with Tab(websocket_url) as tab:
                login = ""
                for _ in range(10):
                    login = str(await tab.evaluate(USER_LOGIN_JS) or "").strip()
                    if login:
                        break
                    await asyncio.sleep(0.5)
                if login.lower() != GITHUB_USERNAME:
                    continue
                state = await tab.evaluate(PAGE_STATE_JS)
            validate_workflow_page(state)
            return candidate
        except WatchdogError:
            continue
        except Exception:
            continue
    raise WatchdogError("未找到内容可用的目标工作流标签")


async def restore_target(
    target_id: str, cdp_url: str, tab: Tab | None = None
) -> bool:
    try:
        current = next(
            target for target in page_targets(get_targets(cdp_url))
            if target.get("id") == target_id
        )
    except (StopIteration, Exception):
        return False

    if normalized_url(current.get("url", "")) != TARGET_URL:
        try:
            if tab is not None:
                await tab.evaluate(f"location.href={json.dumps(TARGET_URL)}")
            else:
                async with Tab(current["webSocketDebuggerUrl"]) as restore_tab:
                    await restore_tab.evaluate(f"location.href={json.dumps(TARGET_URL)}")
        except Exception:
            pass

    for _ in range(30):
        await asyncio.sleep(0.5)
        try:
            current = next(
                target for target in page_targets(get_targets(cdp_url))
                if target.get("id") == target_id
            )
        except (StopIteration, Exception):
            continue
        if normalized_url(current.get("url", "")) == TARGET_URL:
            return True
    return False


async def run(args: argparse.Namespace) -> str:
    try:
        initial_targets = get_targets(args.cdp_url)
    except Exception as exc:
        return f"无法访问 Chrome CDP（{exc}），需要人工处理。"

    target_tab: Tab | None = None
    target_id: str | None = None
    result = "未确认执行成功，需要人工检查。"
    try:
        candidates = await open_target_if_needed(initial_targets, args.cdp_url)
        target = await choose_target(candidates)
        target_id = target["id"]
        async with Tab(target["webSocketDebuggerUrl"]) as tab:
            target_tab = tab
            await tab.reload_and_wait()
            state = None
            validation_error: WatchdogError | None = None
            for _ in range(30):
                await asyncio.sleep(0.5)
                state = await tab.evaluate(PAGE_STATE_JS)
                try:
                    validate_workflow_page(state)
                    validation_error = None
                    break
                except WatchdogError as exc:
                    validation_error = exc
            if validation_error is not None:
                raise validation_error
            latest = latest_run(state)
            if latest is None:
                raise WatchdogError("找不到可靠的最后一次执行时间，需要人工处理")

            latest_time = datetime.fromisoformat(
                str(latest["datetime"]).replace("Z", "+00:00")
            ).astimezone(timezone.utc)
            age = (datetime.now(timezone.utc) - latest_time).total_seconds()
            if age < args.threshold:
                result = "检查完成：最近执行距现在不足7分钟，未执行"
                return result

            before_hrefs = {
                str(run.get("href")) for run in state.get("runs") or []
            }
            # 打开 Run workflow 菜单并找确认按钮。菜单用 JS click() 打开
            # （真实坐标点击对该 summary 无效，见 OPEN_RUN_MENU_JS 注释）；
            # 每轮失败后强制收起菜单再重试，最多 3 轮。
            confirmed_click = False
            menu_diag = None
            for attempt in range(3):
                opened = await tab.evaluate(OPEN_RUN_MENU_JS)
                if not opened:
                    raise WatchdogError(
                        "未找到 Run workflow 按钮，可能未登录或无权限"
                    )
                for _ in range(20):
                    await asyncio.sleep(0.5)
                    if await tab.evaluate(CONFIRM_AND_CLICK_JS):
                        confirmed_click = True
                        break
                if confirmed_click:
                    break
                await tab.evaluate(CLOSE_MENUS_JS)
                menu_diag = await tab.evaluate(MENU_DIAG_JS)
            if not confirmed_click:
                raise WatchdogError(
                    "打开 Run workflow 菜单后未能点击确认按钮（已重试 3 轮），"
                    f"页面诊断：{json.dumps(menu_diag, ensure_ascii=False)}"
                )

            confirmed_run: dict[str, Any] | None = None
            for _ in range(20):
                await asyncio.sleep(1)
                current_state = await tab.evaluate(PAGE_STATE_JS)
                for run in current_state.get("runs") or []:
                    if str(run.get("href")) not in before_hrefs:
                        confirmed_run = run
                        break
                if confirmed_run:
                    break
            if confirmed_run:
                result = "已手动执行：运行列表出现新记录"
            else:
                result = "未确认执行成功：点击后未在页面看到新的运行记录，需要人工检查"
    except WatchdogError as exc:
        result = f"{exc}。"
    except Exception as exc:
        result = f"脚本异常（{exc}），需要人工处理。"
    finally:
        if target_id is not None:
            restored = await restore_target(target_id, args.cdp_url)
            if not restored:
                result += "；目标标签未能恢复到工作流页面，需要人工处理"

    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cdp-url", default=DEFAULT_CDP, help="Chrome CDP 地址")
    parser.add_argument(
        "--threshold",
        type=int,
        default=THRESHOLD_SECONDS,
        help="距最近一次运行的最小秒数，默认420",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = asyncio.run(run(args))
    write_log(result)
    print(result, flush=True)


if __name__ == "__main__":
    main()
