import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowClockwise, ArrowSquareOut, ArrowsOutSimple, Gauge, SpinnerGap, WarningCircle } from "@phosphor-icons/react";

export function ObservabilityView({ readStatus }) {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [reload, setReload] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const screen = useRef(null);
  const check = useCallback(async () => {
    try { setStatus(await readStatus()); setError(""); }
    catch { setError("暂时无法连接监控服务，请重试。"); }
  }, [readStatus]);
  useEffect(() => {
    check();
    const timer = window.setInterval(check, 30000);
    return () => window.clearInterval(timer);
  }, [check]);
  const available = status?.available && !error;
  const refresh = () => { setLoaded(false); setReload((value) => value + 1); check(); };
  const fullscreen = async () => {
    try { await screen.current?.requestFullscreen(); }
    catch { setNotice("浏览器未允许全屏，可使用“独立打开”查看大屏。"); }
  };
  return (
    <section className="observability-screen" ref={screen} aria-label="RevGuard 可观测大屏">
      <div className="observability-toolbar">
        <div className="observability-heading"><Gauge weight="duotone" /><div><h2>运行与资金恢复</h2><p>全部案件 · 实时运行指标 · 合成业务数据 · 15 秒刷新</p></div></div>
        <div className="observability-actions">
          <span className="observability-mode">Grafana · 只读</span>
          <button className="icon-button" onClick={refresh}><ArrowClockwise />刷新</button>
          <button className="icon-button" onClick={fullscreen} disabled={!available}><ArrowsOutSimple />全屏</button>
          {available && <a className="icon-button" href={status.embed_url} target="_blank" rel="noopener noreferrer"><ArrowSquareOut />独立打开</a>}
        </div>
      </div>
      {notice && <p className="observability-notice" role="status">{notice}</p>}
      <div className="observability-frame-wrap">
        {!status && !error && <div className="observability-placeholder"><SpinnerGap className="spin" /><strong>正在连接 Grafana 看板…</strong></div>}
        {(error || (status && !available)) && <div className="observability-placeholder"><WarningCircle /><strong>{error || status.message || "Grafana 暂时不可用"}</strong><p>案件处理仍可使用。监控恢复后会重新连接。</p><button className="icon-button" onClick={refresh}>重新连接</button></div>}
        {available && <>
          {!loaded && <div className="observability-loading"><SpinnerGap className="spin" />正在加载实时面板…</div>}
          <iframe key={reload} src={status.embed_url} title="Grafana：RevGuard 运行与资金恢复" referrerPolicy="same-origin" onLoad={() => setLoaded(true)} onError={() => setError("看板加载失败，请重新连接。")} />
        </>}
      </div>
    </section>
  );
}
