"use client";

import { useEffect, useState } from "react";
import {
  deleteListeningClip,
  deleteListeningSource,
  ingestListening,
  listListeningSources,
  listSourceClips,
  updateListeningClip,
  type ListeningClip,
  type ListeningClipData,
  type ListeningSource,
} from "@/lib/api";

type Tab = "add" | "library";

export default function Listening() {
  const [tab, setTab] = useState<Tab>("library");
  const [sources, setSources] = useState<ListeningSource[] | null>(null);

  function reload() {
    listListeningSources().then(setSources).catch(() => setSources([]));
  }

  useEffect(reload, []);

  return (
    <>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: "1.25rem" }}>
        <h2 style={{ margin: 0 }}>Listening</h2>
        <div className="toggle">
          <button className={`seg ${tab === "library" ? "active" : ""}`} onClick={() => setTab("library")}>
            Library
          </button>
          <button className={`seg ${tab === "add" ? "active" : ""}`} onClick={() => setTab("add")}>
            Add source
          </button>
        </div>
      </div>

      {tab === "add" ? (
        <AddSource
          onAdded={() => {
            reload();
            setTab("library");
          }}
        />
      ) : (
        <Library sources={sources} onChanged={reload} />
      )}
    </>
  );
}

// --- add a source: a local video path + its SRT -----------------------------

function AddSource({ onAdded }: { onAdded: () => void }) {
  const [title, setTitle] = useState("");
  const [video, setVideo] = useState<File | null>(null);
  const [srt, setSrt] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ready = title.trim() && video && srt;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!ready) return;
    setBusy(true);
    setError(null);
    try {
      await ingestListening(title.trim(), video!, srt!);
      onAdded();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add this source.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card" onSubmit={submit}>
      <p className="muted" style={{ marginTop: 0 }}>
        Choose a video file and its subtitle (.srt) file. Claude curates the most useful
        conversational clips; the video is stored and streamed back as you review.
      </p>

      <label className="field">
        <span>Title</span>
        <input type="text" value={title} placeholder="e.g. Dark — S1E1" onChange={(e) => setTitle(e.target.value)} />
      </label>

      <label className="field">
        <span>Video file</span>
        <input
          type="file"
          accept="video/*,audio/*"
          onChange={(e) => setVideo(e.target.files?.[0] ?? null)}
        />
      </label>

      <label className="field">
        <span>Subtitle file (.srt)</span>
        <input
          type="file"
          accept=".srt,text/plain"
          onChange={(e) => setSrt(e.target.files?.[0] ?? null)}
        />
      </label>

      {error && <p className="error" style={{ margin: "0.4rem 0 0" }}>{error}</p>}

      <div className="card-draft-actions">
        <button type="submit" disabled={busy || !ready}>
          {busy ? "Uploading & curating…" : "Add source"}
        </button>
      </div>
    </form>
  );
}

// --- library: sources, each expandable into its clips -----------------------

function Library({
  sources,
  onChanged,
}: {
  sources: ListeningSource[] | null;
  onChanged: () => void;
}) {
  if (sources === null) return <p className="muted">Loading…</p>;
  if (sources.length === 0) {
    return (
      <div className="card">
        <p style={{ margin: 0 }}>
          No listening sources yet. Use <strong>Add source</strong> to turn a video + SRT into clips.
        </p>
      </div>
    );
  }

  return (
    <>
      {sources.map((s) => (
        <SourceRow key={s.id} source={s} onChanged={onChanged} />
      ))}
    </>
  );
}

function SourceRow({ source, onChanged }: { source: ListeningSource; onChanged: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const [clips, setClips] = useState<ListeningClip[] | null>(null);

  function loadClips() {
    listSourceClips(source.id).then(setClips).catch(() => setClips([]));
  }

  function toggle() {
    if (!expanded && clips === null) loadClips();
    setExpanded((v) => !v);
  }

  async function removeSource() {
    if (!confirm(`Delete "${source.title}" and all its clips?`)) return;
    await deleteListeningSource(source.id);
    onChanged();
  }

  function removeClip(id: number) {
    setClips((c) => (c ?? []).filter((x) => x.id !== id));
  }

  return (
    <div className="card manage-card-open">
      <div className="manage-card">
        <button className="manage-card-body manage-card-toggle" onClick={toggle} aria-expanded={expanded}>
          <span>{source.title}</span>
          <span className="muted manage-meta">{source.clip_count} clips</span>
        </button>
        <div className="manage-card-actions">
          <button className="ghost" onClick={removeSource} title="Delete source">
            Delete
          </button>
        </div>
      </div>

      {expanded && (
        <div style={{ marginTop: "0.8rem" }}>
          {clips === null ? (
            <p className="muted">Loading clips…</p>
          ) : clips.length === 0 ? (
            <p className="muted">No clips.</p>
          ) : (
            clips.map((clip) => (
              <ClipRow
                key={clip.id}
                clip={clip}
                onSaved={(u) => setClips((c) => (c ?? []).map((x) => (x.id === u.id ? u : x)))}
                onDelete={() => removeClip(clip.id)}
              />
            ))
          )}
        </div>
      )}
    </div>
  );
}

function fmt(ms: number): string {
  const total = Math.round(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function ClipRow({
  clip,
  onSaved,
  onDelete,
}: {
  clip: ListeningClip;
  onSaved: (c: ListeningClip) => void;
  onDelete: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<ListeningClipData>(clip);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      onSaved(await updateListeningClip(clip.id, draft));
      setEditing(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save the clip.");
    } finally {
      setSaving(false);
    }
  }

  async function remove() {
    await deleteListeningClip(clip.id);
    onDelete();
  }

  if (editing) {
    return (
      <div className="clip-row">
        <label className="field">
          <span>German transcript</span>
          <textarea
            rows={2}
            value={draft.transcript_de}
            onChange={(e) => setDraft({ ...draft, transcript_de: e.target.value })}
          />
        </label>
        <label className="field">
          <span>English</span>
          <textarea
            rows={2}
            value={draft.transcript_en}
            onChange={(e) => setDraft({ ...draft, transcript_en: e.target.value })}
          />
        </label>
        <div className="row" style={{ gap: "0.6rem" }}>
          <label className="field" style={{ flex: 1 }}>
            <span>Difficulty</span>
            <input
              type="text"
              value={draft.difficulty}
              onChange={(e) => setDraft({ ...draft, difficulty: e.target.value })}
            />
          </label>
          <label className="field" style={{ flex: 2 }}>
            <span>Topic</span>
            <input
              type="text"
              value={draft.topic}
              onChange={(e) => setDraft({ ...draft, topic: e.target.value })}
            />
          </label>
        </div>
        {error && <p className="error" style={{ margin: "0.4rem 0 0" }}>{error}</p>}
        <div className="card-draft-actions">
          <button onClick={save} disabled={saving}>
            {saving ? "Saving…" : "Save changes"}
          </button>
          <button className="ghost" onClick={() => setEditing(false)} disabled={saving}>
            Cancel
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="clip-row">
      <div className="manage-card">
        <div className="manage-card-body">
          <span>{clip.transcript_de}</span>
          <span className="muted" style={{ display: "block", margin: "0.2rem 0 0" }}>
            {clip.transcript_en}
          </span>
          <span className="muted manage-meta">
            {fmt(clip.start_ms)}–{fmt(clip.end_ms)} · {clip.difficulty} · {clip.topic} · due {clip.due}
          </span>
        </div>
        <div className="manage-card-actions">
          <button
            className="ghost"
            onClick={() => {
              setDraft(clip);
              setEditing(true);
            }}
            title="Edit clip"
          >
            Edit
          </button>
          <button className="ghost" onClick={remove} title="Delete clip">
            Delete
          </button>
        </div>
      </div>
    </div>
  );
}
