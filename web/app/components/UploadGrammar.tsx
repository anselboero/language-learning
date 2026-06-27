"use client";

import { useState } from "react";
import { ingestPdf } from "@/lib/api";

interface Props {
  kind: "theory" | "practice";
  title: string;
  description: string;
}

export default function UploadGrammar({ kind, title, description }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleUpload() {
    if (!file) return;
    setBusy(true);
    setStatus(null);
    setError(null);
    try {
      const res = await ingestPdf(kind, file);
      const parts = Object.entries(res).map(([k, v]) => `${v} ${k.replace(/_/g, " ")}`);
      setStatus(`Done — ${parts.join(", ")}.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ingestion failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <h2>{title}</h2>
      <p className="muted">{description}</p>
      <div className="row">
        <input
          type="file"
          accept="application/pdf"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
        <button onClick={handleUpload} disabled={!file || busy}>
          {busy ? "Ingesting…" : "Ingest"}
        </button>
      </div>
      {busy && (
        <p className="muted" style={{ marginBottom: 0 }}>
          A full book is processed page-window by page-window — this can take several minutes.
        </p>
      )}
      {status && <p style={{ marginBottom: 0 }}>{status}</p>}
      {error && <p className="error">{error}</p>}
    </div>
  );
}
