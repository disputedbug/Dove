"use client";

import Image from "next/image";
import { useEffect, useMemo, useState } from "react";

type JobStatus = "queued" | "running" | "done" | "failed";

export default function Home() {
  const apiBase = useMemo(
    () => process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000",
    []
  );

  const [baseVideo, setBaseVideo] = useState<File | null>(null);
  const [recipients, setRecipients] = useState<File | null>(null);
  const [namePosition, setNamePosition] = useState<"start" | "end">("start");
  const [text, setText] = useState("{name}");
  const [lang, setLang] = useState("hi");
  const [ttsProvider, setTtsProvider] = useState("gtts");
  const [ttsCmd, setTtsCmd] = useState("");
  const [silenceDb, setSilenceDb] = useState("-30");
  const [silenceDur, setSilenceDur] = useState("0.3");
  const [convertMov, setConvertMov] = useState(true);
  const [convertCrf, setConvertCrf] = useState("20");
  const [convertPreset, setConvertPreset] = useState("medium");
  const [convertAudioBitrate, setConvertAudioBitrate] = useState("160k");
  const [convertLoading, setConvertLoading] = useState(false);
  const [convertError, setConvertError] = useState<string | null>(null);
  const [convertProgress, setConvertProgress] = useState(0);
  const [convertedFile, setConvertedFile] = useState<File | null>(null);
  const [convertedFileName, setConvertedFileName] = useState<string | null>(
    null
  );

  const [jobId, setJobId] = useState<string | null>(null);
  const [status, setStatus] = useState<JobStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [downloadUrl, setDownloadUrl] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!jobId || status === "done" || status === "failed") {
      return;
    }
    const interval = setInterval(async () => {
      try {
        const res = await fetch(`${apiBase}/jobs/${jobId}`);
        if (!res.ok) {
          return;
        }
        const data = await res.json();
        setStatus(data.status);
        setError(data.error ?? null);
        if (data.status === "done" && data.download_url) {
          setDownloadUrl(`${apiBase}${data.download_url}`);
        }
      } catch (err) {
        setError((err as Error).message);
      }
    }, 2000);
    return () => clearInterval(interval);
  }, [apiBase, jobId, status]);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    setDownloadUrl(null);
    setSubmitting(true);

    try {
      const videoToUse = convertMov && convertedFile ? convertedFile : baseVideo;
      if (!videoToUse || !recipients) {
        throw new Error("Please attach both base video and recipients file.");
      }

      const form = new FormData();
      form.append("base_video", videoToUse);
      form.append("recipients", recipients);
      form.append("name_position", namePosition);
      form.append("text", text);
      form.append("lang", lang);
      form.append("tts_provider", ttsProvider);
      form.append("tts_cmd", ttsCmd);
      form.append("silence_db", silenceDb);
      form.append("silence_dur", silenceDur);
      form.append("convert_mov", String(convertMov));

      const res = await fetch(`${apiBase}/jobs`, {
        method: "POST",
        body: form,
      });
      if (!res.ok) {
        const message = await res.text();
        throw new Error(message || "Failed to create job.");
      }
      const data = await res.json();
      setJobId(data.job_id);
      setStatus(data.status);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  const fetchWithProgress = async (
    url: string,
    options: RequestInit,
    onProgress: (value: number) => void
  ): Promise<Blob> => {
    const res = await fetch(url, options);
    if (!res.ok) {
      const message = await res.text();
      throw new Error(message || "Request failed.");
    }
    const contentLength = res.headers.get("content-length");
    if (!res.body || !contentLength) {
      const blob = await res.blob();
      onProgress(100);
      return blob;
    }
    const total = Number(contentLength);
    const reader = res.body.getReader();
    let received = 0;
    const chunks: Uint8Array[] = [];
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (value) {
        chunks.push(value);
        received += value.length;
        onProgress(Math.min(100, Math.round((received / total) * 100)));
      }
    }
    return new Blob(chunks);
  };

  const handleConvert = async () => {
    setConvertError(null);
    setConvertLoading(true);
    setConvertProgress(0);
    try {
      if (!baseVideo) {
        throw new Error("Please attach a base video to convert.");
      }
      const form = new FormData();
      form.append("base_video", baseVideo);
      form.append("crf", convertCrf);
      form.append("preset", convertPreset);
      form.append("audio_bitrate", convertAudioBitrate);

      const blob = await fetchWithProgress(
        `${apiBase}/convert`,
        {
          method: "POST",
          body: form,
        },
        setConvertProgress
      );
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      const downloadName =
        baseVideo.name.replace(/\\.[^/.]+$/, "") + "_converted.mp4";
      link.download = downloadName;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);

      const file = new File([blob], downloadName, { type: "video/mp4" });
      setConvertedFile(file);
      setConvertedFileName(downloadName);
    } catch (err) {
      setConvertError((err as Error).message);
    } finally {
      setConvertLoading(false);
      setConvertProgress(100);
    }
  };

  return (
    <div className="relative min-h-screen overflow-hidden bg-[url('/wall.jpg')] bg-cover bg-center px-6 py-10 text-[--ink]">
      <div className="absolute inset-0 bg-white/80 backdrop-blur-[2px]" />
      <div className="relative z-10 mx-auto grid w-full max-w-6xl gap-10 lg:grid-cols-[1.1fr_0.9fr]">
        <section className="flex flex-col gap-6">
          <div className="rounded-3xl border border-black/10 bg-[--card] p-8 shadow-[0_30px_80px_-40px_rgba(0,0,0,0.45)]">
            <div className="flex items-center gap-3">
              <span className="inline-flex h-12 w-12 items-center justify-center rounded-2xl border border-black/10 bg-white text-black">
                <Image
                  src="/vidx-logo.svg"
                  alt="VidX logo"
                  width={34}
                  height={34}
                />
              </span>
              <div>
                <p className="text-sm uppercase tracking-[0.2em] text-black/50">
                  VidX Studio
                </p>
                <h1 className="text-4xl font-semibold leading-tight">
                  Personalized video messages, one click away.
                </h1>
              </div>
            </div>
            <p className="mt-6 text-lg text-black/70">
              Upload a base video and a recipients file. VidX will add each
              person’s name at the beginning or right after the speaker stops,
              then package everything into a downloadable zip.
            </p>
            <div className="mt-8 grid gap-4 sm:grid-cols-2">
              <div className="rounded-2xl border border-black/10 bg-white/80 p-4">
                <p className="text-xs uppercase tracking-[0.2em] text-black/50">
                  API Base
                </p>
                <p className="mt-2 truncate text-sm font-medium">{apiBase}</p>
              </div>
              <div className="rounded-2xl border border-black/10 bg-white/80 p-4">
                <p className="text-xs uppercase tracking-[0.2em] text-black/50">
                  Status
                </p>
                <p className="mt-2 text-sm font-medium">
                  {status ?? "Idle"}
                </p>
              </div>
            </div>
          </div>

          <div className="grid gap-4 sm:grid-cols-3">
            {[
              "Upload base video",
              "Add recipients sheet",
              "Download personalized set",
            ].map((step, idx) => (
              <div
                key={step}
                className="rounded-2xl border border-black/10 bg-white/80 p-4"
              >
                <p className="text-xs uppercase tracking-[0.2em] text-black/50">
                  Step {idx + 1}
                </p>
                <p className="mt-2 text-sm font-medium">{step}</p>
              </div>
            ))}
          </div>

          <div className="rounded-2xl border border-black/10 bg-white/80 p-4 shadow-[0_10px_30px_-28px_rgba(0,0,0,0.4)]">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-sm font-semibold">Convert only</p>
              <span className="text-xs uppercase tracking-[0.2em] text-black/50">
                MOV → MP4
              </span>
            </div>
            <div className="mt-3 grid gap-3 sm:grid-cols-3">
              <div>
                <label className="text-[11px] font-medium">CRF</label>
                <input
                  value={convertCrf}
                  onChange={(e) => setConvertCrf(e.target.value)}
                  className="mt-1 w-full rounded-xl border border-black/10 bg-white px-3 py-2 text-sm"
                />
              </div>
              <div>
                <label className="text-[11px] font-medium">Preset</label>
                <input
                  value={convertPreset}
                  onChange={(e) => setConvertPreset(e.target.value)}
                  className="mt-1 w-full rounded-xl border border-black/10 bg-white px-3 py-2 text-sm"
                />
              </div>
              <div>
                <label className="text-[11px] font-medium">Audio bitrate</label>
                <input
                  value={convertAudioBitrate}
                  onChange={(e) => setConvertAudioBitrate(e.target.value)}
                  className="mt-1 w-full rounded-xl border border-black/10 bg-white px-3 py-2 text-sm"
                />
              </div>
            </div>
            <button
              type="button"
              onClick={handleConvert}
              disabled={convertLoading}
              className="mt-3 w-full rounded-xl border border-black/20 bg-[#1f1f1f] px-4 py-2 text-sm font-semibold text-white shadow-[0_12px_28px_-18px_rgba(0,0,0,0.7)] transition hover:-translate-y-0.5 hover:scale-[1.01] hover:shadow-[0_22px_50px_-26px_rgba(0,0,0,0.85)] disabled:opacity-60"
            >
              {convertLoading ? "Converting..." : "Convert and download MP4"}
            </button>
            <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-black/10">
              <div
                className="h-full bg-black/70 transition-all"
                style={{ width: `${convertProgress}%` }}
              />
            </div>
            {convertError && (
              <p className="mt-2 text-sm text-[--accent]">Error: {convertError}</p>
            )}
          </div>
        </section>

        <section className="rounded-3xl border border-black/10 bg-white p-6 shadow-[0_24px_70px_-42px_rgba(0,0,0,0.5)]">
          <h2 className="text-2xl font-semibold">Create a job</h2>
          <p className="mt-2 text-sm text-black/60">
            Fill in the inputs and submit. Keep this tab open while the job runs.
          </p>

          <form className="mt-6 space-y-4" onSubmit={handleSubmit}>
            <div>
              <label className="text-sm font-medium">Base video</label>
              <div className="mt-2">
                <input
                  id="base_video"
                  type="file"
                  accept="video/*"
                  onChange={(e) => setBaseVideo(e.target.files?.[0] ?? null)}
                  className="hidden"
                />
                <label
                  htmlFor="base_video"
                  className="flex w-full cursor-pointer items-center justify-center rounded-xl border border-black/10 bg-white px-3 py-2 text-center text-sm text-black/70"
                >
                  {baseVideo ? baseVideo.name : "No file chosen"}
                </label>
              </div>
            </div>
            <div>
              <label className="text-sm font-medium">Recipients file</label>
              <div className="mt-2">
                <input
                  id="recipients_file"
                  type="file"
                  accept=".xlsx,.csv"
                  onChange={(e) => setRecipients(e.target.files?.[0] ?? null)}
                  className="hidden"
                />
                <label
                  htmlFor="recipients_file"
                  className="flex w-full cursor-pointer items-center justify-center rounded-xl border border-black/10 bg-white px-3 py-2 text-center text-sm text-black/70"
                >
                  {recipients ? recipients.name : "No file chosen"}
                </label>
              </div>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <label className="text-sm font-medium">Name position</label>
                <select
                  value={namePosition}
                  onChange={(e) =>
                    setNamePosition(e.target.value as "start" | "end")
                  }
                  className="mt-2 w-full rounded-xl border border-black/10 bg-white px-3 py-2 text-sm"
                >
                  <option value="start">Start</option>
                  <option value="end">After speaker stops</option>
                </select>
              </div>
              <div>
                <label className="text-sm font-medium">Language</label>
                <input
                  value={lang}
                  onChange={(e) => setLang(e.target.value)}
                  className="mt-2 w-full rounded-xl border border-black/10 bg-white px-3 py-2 text-sm"
                />
              </div>
            </div>

            <div>
              <label className="text-sm font-medium">Speech text</label>
              <input
                value={text}
                onChange={(e) => setText(e.target.value)}
                className="mt-2 w-full rounded-xl border border-black/10 bg-white px-3 py-2 text-sm"
              />
              <p className="mt-1 text-xs text-black/50">
                Keep <code className="px-1">{"{name}"}</code> in the text.
              </p>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <label className="text-sm font-medium">TTS provider</label>
                <select
                  value={ttsProvider}
                  onChange={(e) => setTtsProvider(e.target.value)}
                  className="mt-2 w-full rounded-xl border border-black/10 bg-white px-3 py-2 text-sm"
                >
                  <option value="gtts">gTTS</option>
                  <option value="command">External command</option>
                  <option value="none">None</option>
                </select>
              </div>
              <div>
                <label className="text-sm font-medium">Silence detect (dB)</label>
                <input
                  value={silenceDb}
                  onChange={(e) => setSilenceDb(e.target.value)}
                  className="mt-2 w-full rounded-xl border border-black/10 bg-white px-3 py-2 text-sm"
                />
              </div>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <label className="text-sm font-medium">
                  TTS command (optional)
                </label>
                <input
                  value={ttsCmd}
                  onChange={(e) => setTtsCmd(e.target.value)}
                  placeholder='python3 /path/to/tts.py --text "{text}" --out "{out}"'
                  className="mt-2 w-full rounded-xl border border-black/10 bg-white px-3 py-2 text-sm"
                />
              </div>
              <div>
                <label className="text-sm font-medium">
                  Silence duration (sec)
                </label>
                <input
                  value={silenceDur}
                  onChange={(e) => setSilenceDur(e.target.value)}
                  className="mt-2 w-full rounded-xl border border-black/10 bg-white px-3 py-2 text-sm"
                />
              </div>
            </div>

            <div className="flex items-center gap-3 rounded-2xl border border-black/10 bg-[--card] px-4 py-3">
              <input
                id="convert_mov"
                type="checkbox"
                checked={convertMov}
                onChange={(e) => {
                  const checked = e.target.checked;
                  setConvertMov(checked);
                  if (!checked) {
                    setConvertedFile(null);
                    setConvertedFileName(null);
                  }
                }}
                className="h-4 w-4 rounded border-black/20"
              />
              <label htmlFor="convert_mov" className="text-sm font-medium">
                Convert .MOV to MP4 before processing
              </label>
            </div>

            {convertMov && convertedFileName && (
              <div className="rounded-2xl border border-black/10 bg-white/70 px-4 py-3 text-xs">
                Using converted file:{" "}
                <span className="font-semibold">{convertedFileName}</span>
              </div>
            )}

            <button
              type="submit"
              disabled={submitting}
              className="mt-2 w-full rounded-2xl bg-[#1f1f1f] px-4 py-3 text-sm font-semibold text-white shadow-[0_12px_28px_-18px_rgba(0,0,0,0.7)] transition hover:-translate-y-0.5 hover:scale-[1.01] hover:shadow-[0_22px_50px_-26px_rgba(0,0,0,0.85)] disabled:opacity-60"
            >
              {submitting ? "Submitting..." : "Generate videos"}
            </button>
          </form>

          <div className="mt-6 space-y-3 rounded-2xl border border-black/10 bg-[--card] p-4 text-sm">
            <p className="font-medium">Job status</p>
            <p>Status: {status ?? "Idle"}</p>
            {jobId && <p>Job ID: {jobId}</p>}
            {error && <p className="text-[--accent]">Error: {error}</p>}
            {downloadUrl && (
              <a
                className="inline-flex w-full items-center justify-center gap-2 rounded-2xl bg-[#1f1f1f] px-4 py-3 text-sm font-semibold text-white shadow-[0_12px_28px_-18px_rgba(0,0,0,0.7)] transition hover:-translate-y-0.5 hover:scale-[1.01] hover:shadow-[0_22px_50px_-26px_rgba(0,0,0,0.85)]"
                href={downloadUrl}
              >
                Download ZIP
              </a>
            )}
          </div>
        </section>
      </div>

      <footer className="relative z-10 mx-auto mt-10 flex w-full max-w-6xl flex-wrap items-center justify-between gap-3 border-t border-black/10 pt-6 text-sm font-semibold text-black/70">
        <p className="min-w-[260px] flex-1">
          Ownership of &quot;VidX&quot; vests with Trine Engineering Private Limited
          (TEPL). Use is permitted strictly under license.
        </p>
        <p className="min-w-[240px] text-right text-xs font-medium text-black/60">
          Designed and developed by TEPL Development team.
        </p>
      </footer>
    </div>
  );
}
