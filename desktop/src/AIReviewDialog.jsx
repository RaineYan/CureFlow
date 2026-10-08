import { useEffect, useMemo, useRef, useState } from "react";

function evidenceChoices(result) {
  const choices = new Map();
  for (const metric of result.metrics || []) {
    for (const ref of [metric.artifactRef, ...(metric.evidenceRefs || [])]) {
      if (typeof ref === "string" && ref) choices.set(ref, metric.label || ref);
    }
  }
  for (const tool of result.analysisTools?.executedTools || []) {
    if (typeof tool.evidenceRef === "string" && tool.evidenceRef) {
      choices.set(tool.evidenceRef, tool.label || tool.evidenceRef);
    }
  }
  return [...choices].map(([ref, label]) => ({ ref, label }));
}

export default function AIReviewDialog({ result, kind, index, request, onClose, onApplied }) {
  const initial = result.aiInterpretation?.[kind === "finding" ? "findings" : "actions"]?.[index];
  const [decision, setDecision] = useState("revise");
  const [heading, setHeading] = useState(initial?.title || initial?.action || "");
  const [body, setBody] = useState(initial?.explanation || initial?.reason || "");
  const [refs, setRefs] = useState(initial?.evidenceRefs || []);
  const [note, setNote] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const firstField = useRef(null);
  const choices = useMemo(() => evidenceChoices(result), [result]);

  useEffect(() => {
    firstField.current?.focus();
    const closeOnEscape = event => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, []);

  if (!initial) return null;
  const selectedRefs = new Set(refs);
  const fields = kind === "finding" ? { title: heading, explanation: body } : { action: heading, reason: body };
  const payload = {
    runId: result.runId,
    inputFingerprint: result.input?.inputFingerprint,
    expectedRevision: result.interpretationReview?.revision ?? 0,
    kind, index, decision, ...fields, evidenceRefs: refs,
    reviewerNote: note, confirmedAgainstSource: confirmed,
  };
  async function submit(apply) {
    setBusy(true); setError("");
    try {
      const response = await request(`/api/interpretation/${apply ? "apply" : "preview"}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
      if (apply) onApplied(response);
      else setPreview(response);
    } catch (caught) {
      setError(caught.message || "无法核验这项修正");
      setPreview(null);
    } finally { setBusy(false); }
  }
  function edit(setter, value) { setter(value); setPreview(null); }
  function toggleRef(ref) {
    setRefs(current => current.includes(ref) ? current.filter(item => item !== ref) : [...current, ref]);
    setPreview(null);
  }

  return <div className="review-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="review-dialog ai-review-dialog" role="dialog" aria-modal="true" aria-label="复核 AI 陈述">
      <header><div><small>人工复核 · {result.input?.filename} · 第 {index + 1} 项</small><h2>核对 AI {kind === "finding" ? "结论" : "建议"}</h2><p>先对照原始证据。撤回错误陈述，或写入已经核实的表述；系统只校验证据身份，不替你证明文字内容正确。</p></div><button type="button" className="secondary" onClick={onClose}>关闭</button></header>
      <div className="ai-review-scroll">
        <div className="ai-review-before"><b>原始陈述</b><p>{initial.title || initial.action}：{initial.explanation || initial.reason}</p><small>原引用：{initial.evidenceRefs?.join("、") || "无"}</small></div>
        <fieldset className="ai-review-choice"><legend>处理方式</legend><label><input ref={firstField} type="radio" checked={decision === "revise"} onChange={() => edit(setDecision, "revise")}/>修正并保留</label><label><input type="radio" checked={decision === "reject"} onChange={() => edit(setDecision, "reject")}/>撤回该项</label></fieldset>
        {decision === "revise" && <><label className="ai-review-field">{kind === "finding" ? "修正后的标题" : "修正后的建议"}<input value={heading} maxLength={180} onChange={event => edit(setHeading, event.target.value)}/></label><label className="ai-review-field">{kind === "finding" ? "修正后的解释" : "修正后的依据"}<textarea value={body} maxLength={1200} rows={3} onChange={event => edit(setBody, event.target.value)}/></label><fieldset className="ai-review-evidence"><legend>本次运行可引用的证据（至少选一项）</legend>{choices.map(choice => <label key={choice.ref}><input type="checkbox" checked={selectedRefs.has(choice.ref)} onChange={() => toggleRef(choice.ref)}/><span><b>{choice.label}</b><small>{choice.ref}</small></span></label>)}</fieldset></>}
        <label className="ai-review-field">复核依据备注（可选）<textarea value={note} maxLength={500} rows={2} onChange={event => edit(setNote, event.target.value)} placeholder="例如：已核对原始记录和指标口径"/></label>
        <label className="ai-review-confirm"><input type="checkbox" checked={confirmed} onChange={event => edit(setConfirmed, event.target.checked)}/>我已核对原始证据；这项内容或撤回决定由我确认。</label>
        {preview && <div className="ai-review-preview" role="status"><b>预览已通过 · 尚未保存</b><p>受影响：当前 AI 陈述与易读报告。报告将本地重新生成；原始文件、统计工具和其他陈述直接复用，不调用云模型。</p><p>机器已核验引用身份：{preview.verification.evidenceIdentityVerified ? "是" : "撤回项不再引用证据"}；文字真实性仍以你的人工核对为准。</p></div>}
        {error && <p className="error" role="alert">{error}</p>}
      </div>
      <footer><span>原始结果保留；应用后会留下前后版本记录。</span><div><button type="button" className="secondary" disabled={busy || !confirmed} onClick={() => submit(false)}>{busy ? "处理中…" : "先预览并核验"}</button><button type="button" className="primary" disabled={busy || !preview} onClick={() => submit(true)}>应用修正并重建报告</button></div></footer>
    </section>
  </div>;
}
