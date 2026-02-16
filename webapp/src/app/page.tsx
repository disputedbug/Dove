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
  const [voiceSample, setVoiceSample] = useState<File | null>(null);
  const [namePosition, setNamePosition] = useState<"start" | "end">("start");
  const [insertMode, setInsertMode] = useState<"silver" | "gold" | "diamond">(
    "silver"
  );
  const [text, setText] = useState("{name}");
  const [lang, setLang] = useState("hi");
  const [ttsProvider, setTtsProvider] = useState("elevenlabs");
  const [ttsCmd, setTtsCmd] = useState("");
  const [elevenModelId, setElevenModelId] = useState("eleven_multilingual_v2");
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
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [langUI, setLangUI] = useState<"en" | "hi">("en");

  const t = (key: string) => {
    const dict: Record<string, Record<"en" | "hi", string>> = {
      title: {
        en: "Send personalized video messages, one click away.",
        hi: "व्यक्तिगत वीडियो संदेश भेजें, बस एक क्लिक में।",
      },
      blurb: {
        en: "Upload a base video and a recipients file. VidX will add each person’s name at the beginning or right after the speaker stops, then package everything into a downloadable zip.",
        hi: "एक बेस वीडियो और रिसीपिएंट्स फ़ाइल अपलोड करें। VidX हर व्यक्ति का नाम शुरुआत में या बोलने वाले के रुकने के तुरंत बाद जोड़ देगा, और फिर सबको एक डाउनलोड योग्य ज़िप में पैक करेगा।",
      },
      step1: {
        en: "Upload base video & Voice samples",
        hi: "बेस वीडियो और वॉइस सैंपल्स अपलोड करें",
      },
      step2: {
        en: "Add recipients sheet",
        hi: "रिसीपिएंट्स शीट जोड़ें",
      },
      step3: {
        en: "Download personalized set",
        hi: "व्यक्तिगत सेट डाउनलोड करें",
      },
      createJob: { en: "Create a job", hi: "जॉब बनाएँ" },
      fillInputs: {
        en: "Fill in the inputs and submit. Keep this tab open while the job runs.",
        hi: "इनपुट भरें और सबमिट करें। जॉब चलते समय यह टैब खुला रखें।",
      },
      baseVideo: { en: "Base video", hi: "बेस वीडियो" },
      voiceSample: { en: "Voice sample", hi: "वॉइस सैंपल" },
      voiceSampleOptional: { en: "Voice sample (optional)", hi: "वॉइस सैंपल (वैकल्पिक)" },
      recipientsFile: { en: "Recipients file", hi: "रिसीपिएंट्स फ़ाइल" },
      noFile: { en: "No file chosen", hi: "कोई फ़ाइल नहीं चुनी गई" },
      plan: { en: "Plan", hi: "प्लान" },
      namePosition: { en: "Name position", hi: "नाम की स्थिति" },
      language: { en: "Language", hi: "भाषा" },
      speechText: { en: "Speech text", hi: "स्पीच टेक्स्ट" },
      keepName: { en: "Keep {name} in the text.", hi: "टेक्स्ट में {name} रखें।" },
      ttsProvider: { en: "TTS provider", hi: "TTS प्रदाता" },
      silenceDb: { en: "Silence detect (dB)", hi: "साइलेंस डिटेक्ट (dB)" },
      ttsCmd: { en: "TTS command (optional)", hi: "TTS कमांड (वैकल्पिक)" },
      silenceDur: { en: "Silence duration (sec)", hi: "साइलेंस अवधि (सेक)" },
      convertMov: { en: "Convert .MOV to MP4 before processing", hi: ".MOV को MP4 में बदलें" },
      generate: { en: "Generate videos", hi: "वीडियो बनाएँ" },
      submitting: { en: "Submitting...", hi: "सबमिट हो रहा है..." },
      jobStatus: { en: "Job status", hi: "जॉब स्थिति" },
      status: { en: "Status", hi: "स्थिति" },
      downloadZip: { en: "Download ZIP", hi: "ZIP डाउनलोड करें" },
      apiBase: { en: "API Base", hi: "API बेस" },
      basic: { en: "Basic", hi: "बेसिक" },
      advanced: { en: "Advanced", hi: "एडवांस्ड" },
      tooltip: { en: "Every Message, Every Time. The Dove Delivers.", hi: "हर संदेश, हर बार। कबूतर पहुँचाए।" },
    };
    return dict[key]?.[langUI] ?? key;
  };

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
      if (ttsProvider === "elevenlabs" && !voiceSample) {
        throw new Error("Voice sample is required when ElevenLabs is selected.");
      }
      if (voiceSample) form.append("voice_sample", voiceSample);
      form.append("insert_mode", insertMode);
      form.append("name_position", namePosition);
      form.append("text", text);
      form.append("lang", lang);
      form.append("tts_provider", ttsProvider);
      form.append("tts_cmd", ttsCmd);
      form.append("elevenlabs_api_key", "");
      // Voice ID is created server-side by cloning the provided voice sample.
      form.append("elevenlabs_voice_id", "");
      form.append("elevenlabs_model_id", elevenModelId);
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
    const chunks: BlobPart[] = [];
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (value) {
        chunks.push(new Uint8Array(value));
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
      <div className="absolute inset-0 bg-black/10 backdrop-blur-[1px]" />
      <div className="relative z-10 mx-auto grid w-full max-w-6xl gap-10 lg:grid-cols-[1.1fr_0.9fr]">
        <section className="flex flex-col gap-6">
          <div className="rounded-3xl border border-black/10 bg-[--card] p-8 shadow-[0_30px_80px_-40px_rgba(0,0,0,0.45)]">
            <div className="flex items-start justify-between gap-4">
              <div className="flex items-start gap-3">
              <span className="group relative inline-flex h-28 w-28 cursor-pointer items-center justify-center rounded-3xl border border-black/10 bg-white text-black shadow-[0_12px_28px_-22px_rgba(0,0,0,0.45)] transition-transform hover:-translate-y-1 hover:scale-[1.06] active:translate-y-0 active:scale-[0.99]">
                <Image
                  src="/dove.png"
                  alt="VidX dove logo"
                  width={84}
                  height={84}
                />
                <span className="pointer-events-none absolute left-1/2 top-[-10px] z-20 w-max -translate-x-1/2 -translate-y-full rounded-lg bg-black px-3 py-1 text-xs font-semibold text-white opacity-0 shadow-[0_14px_30px_-18px_rgba(0,0,0,0.8)] transition-opacity group-hover:opacity-100">
                  {t("tooltip")}
                </span>
              </span>
              <div>
                <p className="text-sm font-extrabold uppercase tracking-[0.25em] text-black">
                  VidX Studio
                </p>
                <h1 className="mt-3 text-4xl font-semibold leading-tight">
                  {t("title")}
                </h1>
              </div>
              </div>
              <div className="mt-1 flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setLangUI("en")}
                  className={`flex h-7 w-7 items-center justify-center rounded-full border transition ${
                    langUI === "en" ? "border-black" : "border-black/20 opacity-60"
                  }`}
                  aria-label="English"
                >
                  <svg viewBox="0 0 60 60" className="h-5 w-5 rounded-full">
                    <rect width="60" height="60" fill="#012169" />
                    <path
                      d="M0 0 L60 60 M60 0 L0 60"
                      stroke="#ffffff"
                      strokeWidth="12"
                    />
                    <path
                      d="M0 0 L60 60 M60 0 L0 60"
                      stroke="#c8102e"
                      strokeWidth="6"
                    />
                    <rect x="24" width="12" height="60" fill="#ffffff" />
                    <rect y="24" width="60" height="12" fill="#ffffff" />
                    <rect x="27" width="6" height="60" fill="#c8102e" />
                    <rect y="27" width="60" height="6" fill="#c8102e" />
                  </svg>
                </button>
                <button
                  type="button"
                  onClick={() => setLangUI("hi")}
                  className={`flex h-7 w-7 items-center justify-center rounded-full border transition ${
                    langUI === "hi" ? "border-black" : "border-black/20 opacity-60"
                  }`}
                  aria-label="Hindi"
                >
                  <svg viewBox="0 0 60 60" className="h-5 w-5 rounded-full">
                    <rect width="60" height="20" y="0" fill="#FF9933" />
                    <rect width="60" height="20" y="20" fill="#ffffff" />
                    <rect width="60" height="20" y="40" fill="#128807" />
                    <circle cx="30" cy="30" r="5" fill="#000080" />
                  </svg>
                </button>
              </div>
            </div>
            <p className="mt-4 text-lg font-semibold text-black/80">{t("blurb")}</p>
            <div className="mt-8 grid gap-4 sm:grid-cols-2">
              <div className="rounded-2xl border border-black/10 bg-white/80 p-4">
                <p className="text-xs uppercase tracking-[0.2em] text-black/50">
                  {t("apiBase")}
                </p>
                <p className="mt-2 truncate text-sm font-medium">{apiBase}</p>
              </div>
              <div className="rounded-2xl border border-black/10 bg-white/80 p-4">
                <p className="text-xs uppercase tracking-[0.2em] text-black/50">
                  {t("status")}
                </p>
                <p className="mt-2 text-sm font-medium">
                  {status ?? "Idle"}
                </p>
              </div>
            </div>
          </div>

          <div className="grid gap-4 sm:grid-cols-3">
            {[
              t("step1"),
              t("step2"),
              t("step3"),
            ].map((step, idx) => (
              <div
                key={step}
                className="rounded-2xl border border-black/10 bg-white/80 p-4"
              >
                <p className="text-xs uppercase tracking-[0.2em] text-black/50">
                  {`Step ${idx + 1}`}
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
              className="mt-3 w-full rounded-xl border border-black/20 bg-[#1f1f1f] px-4 py-2 text-sm font-semibold text-white shadow-[0_12px_28px_-18px_rgba(0,0,0,0.7)] transition hover:-translate-y-0.5 hover:scale-[1.01] hover:shadow-[0_22px_50px_-26px_rgba(0,0,0,0.85)] active:translate-y-0 active:scale-[0.99] active:shadow-[0_10px_18px_-16px_rgba(0,0,0,0.75)] disabled:opacity-60"
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
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-2xl font-semibold">{t("createJob")}</h2>
            <div className="flex items-center gap-3">
              <label className="inline-flex items-center gap-2 rounded-full border border-black/10 bg-white px-3 py-1 text-xs font-semibold text-black/70">
                <span>{t("basic")}</span>
                <input
                  type="checkbox"
                  checked={showAdvanced}
                  onChange={(e) => setShowAdvanced(e.target.checked)}
                  className="h-4 w-7 appearance-none rounded-full bg-black/10 transition before:block before:h-3 before:w-3 before:translate-x-0.5 before:rounded-full before:bg-black before:transition checked:bg-black/20 checked:before:translate-x-3"
                />
                <span>{t("advanced")}</span>
              </label>
            </div>
          </div>
          <p className="mt-2 text-sm text-black/60">
            {t("fillInputs")}
          </p>

          <form className="mt-6 space-y-4" onSubmit={handleSubmit}>
            <div>
              <label className="text-sm font-medium">{t("baseVideo")}</label>
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
                  {baseVideo ? baseVideo.name : t("noFile")}
                </label>
              </div>
            </div>
            <div>
              <label className="text-sm font-medium">
                {ttsProvider === "elevenlabs"
                  ? t("voiceSample")
                  : t("voiceSampleOptional")}
              </label>
              <div className="mt-2">
                <input
                  id="voice_sample"
                  type="file"
                  accept="audio/*"
                  onChange={(e) => setVoiceSample(e.target.files?.[0] ?? null)}
                  className="hidden"
                />
                <label
                  htmlFor="voice_sample"
                  className="flex w-full cursor-pointer items-center justify-center rounded-xl border border-black/10 bg-white px-3 py-2 text-center text-sm text-black/70"
                >
                  {voiceSample ? voiceSample.name : t("noFile")}
                </label>
              </div>
              {showAdvanced && ttsProvider === "command" && (
                <p className="mt-1 text-xs text-black/50">
                  Used only by external TTS providers (via <code>{"{voice}"}</code>{" "}
                  in TTS command).
                </p>
              )}
            </div>
            <div>
              <label className="text-sm font-medium">{t("recipientsFile")}</label>
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
                  {recipients ? recipients.name : t("noFile")}
                </label>
              </div>
            </div>

            {showAdvanced && (
              <>
                <div className="grid gap-4 sm:grid-cols-2">
                  <div>
                    <label className="text-sm font-medium">{t("plan")}</label>
                    <select
                      value={insertMode}
                      onChange={(e) =>
                        setInsertMode(
                          e.target.value as "silver" | "gold" | "diamond"
                        )
                      }
                      className="mt-2 w-full rounded-xl border border-black/10 bg-white px-3 py-2 text-sm"
                    >
                      <option value="silver">Silver (audio name insert)</option>
                      <option value="gold">Gold (replace generic-name audio)</option>
                      <option value="diamond">Diamond (not implemented)</option>
                    </select>
                  </div>
                  <div>
                    <label className="text-sm font-medium">{t("namePosition")}</label>
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
                </div>

                <div>
                  <label className="text-sm font-medium">{t("language")}</label>
                  <input
                    value={lang}
                    onChange={(e) => setLang(e.target.value)}
                    className="mt-2 w-full rounded-xl border border-black/10 bg-white px-3 py-2 text-sm"
                  />
                </div>

                <div>
                  <label className="text-sm font-medium">{t("speechText")}</label>
                  <input
                    value={text}
                    onChange={(e) => setText(e.target.value)}
                    className="mt-2 w-full rounded-xl border border-black/10 bg-white px-3 py-2 text-sm"
                  />
                  <p className="mt-1 text-xs text-black/50">
                    {t("keepName")}
                  </p>
                </div>

                <div className="grid gap-4 sm:grid-cols-2">
                  <div>
                    <label className="text-sm font-medium">{t("ttsProvider")}</label>
                    <select
                      value={ttsProvider}
                      onChange={(e) => setTtsProvider(e.target.value)}
                      className="mt-2 w-full rounded-xl border border-black/10 bg-white px-3 py-2 text-sm"
                    >
                      <option value="gtts">gTTS</option>
                      <option value="elevenlabs">ElevenLabs</option>
                      <option value="command">External command</option>
                      <option value="none">None</option>
                    </select>
                  </div>
                  <div>
                    <label className="text-sm font-medium">{t("silenceDb")}</label>
                    <input
                      value={silenceDb}
                      onChange={(e) => setSilenceDb(e.target.value)}
                      className="mt-2 w-full rounded-xl border border-black/10 bg-white px-3 py-2 text-sm"
                    />
                  </div>
                </div>

                <div className="grid gap-4 sm:grid-cols-2">
                  <div>
                    <label className="text-sm font-medium">{t("ttsCmd")}</label>
                    <input
                      value={ttsCmd}
                      onChange={(e) => setTtsCmd(e.target.value)}
                      placeholder='python3 /path/to/tts.py --text "{text}" --out "{out}"'
                      className="mt-2 w-full rounded-xl border border-black/10 bg-white px-3 py-2 text-sm"
                    />
                  </div>
                  <div>
                    <label className="text-sm font-medium">{t("silenceDur")}</label>
                    <input
                      value={silenceDur}
                      onChange={(e) => setSilenceDur(e.target.value)}
                      className="mt-2 w-full rounded-xl border border-black/10 bg-white px-3 py-2 text-sm"
                    />
                  </div>
                </div>

                {ttsProvider === "elevenlabs" && (
                  <div className="space-y-3 rounded-2xl border border-black/10 bg-white/70 p-4">
                    <p className="text-sm font-semibold">ElevenLabs settings</p>
                <div className="text-xs text-black/60">
                  API key is loaded from the server environment.
                </div>
                    <div>
                      <label className="text-xs font-medium">Model ID</label>
                      <input
                        value={elevenModelId}
                        onChange={(e) => setElevenModelId(e.target.value)}
                        className="mt-1 w-full rounded-xl border border-black/10 bg-white px-3 py-2 text-sm"
                      />
                    </div>
                    <p className="text-xs text-black/50">
                      When ElevenLabs is selected, VidX clones the provided voice
                      sample on the server and uses it for name generation.
                    </p>
              </div>
            )}

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
                    {t("convertMov")}
                  </label>
                </div>

                {convertMov && convertedFileName && (
                  <div className="rounded-2xl border border-black/10 bg-white/70 px-4 py-3 text-xs">
                    Using converted file:{" "}
                    <span className="font-semibold">{convertedFileName}</span>
                  </div>
                )}
              </>
            )}

            <button
              type="submit"
              disabled={submitting}
              className="mt-2 w-full rounded-2xl bg-[#1f1f1f] px-4 py-3 text-sm font-semibold text-white shadow-[0_12px_28px_-18px_rgba(0,0,0,0.7)] transition hover:-translate-y-0.5 hover:scale-[1.01] hover:shadow-[0_22px_50px_-26px_rgba(0,0,0,0.85)] active:translate-y-0 active:scale-[0.99] active:shadow-[0_10px_18px_-16px_rgba(0,0,0,0.75)] disabled:opacity-60"
            >
              {submitting ? t("submitting") : t("generate")}
            </button>
          </form>

          <div className="mt-6 space-y-3 rounded-2xl border border-black/10 bg-[--card] p-4 text-sm">
            <p className="font-medium">{t("jobStatus")}</p>
            <p>{t("status")}: {status ?? "Idle"}</p>
            {jobId && <p>Job ID: {jobId}</p>}
            {error && <p className="text-[--accent]">Error: {error}</p>}
            {downloadUrl && (
              <a
                className="inline-flex w-full items-center justify-center gap-2 rounded-2xl bg-[#1f1f1f] px-4 py-3 text-sm font-semibold text-white shadow-[0_12px_28px_-18px_rgba(0,0,0,0.7)] transition hover:-translate-y-0.5 hover:scale-[1.01] hover:shadow-[0_22px_50px_-26px_rgba(0,0,0,0.85)] active:translate-y-0 active:scale-[0.99] active:shadow-[0_10px_18px_-16px_rgba(0,0,0,0.75)]"
                href={downloadUrl}
              >
                {t("downloadZip")}
              </a>
            )}
          </div>
        </section>
      </div>

      <footer className="relative z-10 mx-auto mt-10 flex w-full max-w-6xl flex-wrap items-center justify-between gap-3 border-t border-white/30 pt-6 text-sm font-semibold text-white">
        <p className="min-w-[260px] flex-1">
          Ownership of &quot;VidX&quot; vests with Trine Engineering Private Limited
          (TEPL). Use is permitted strictly under license.
        </p>
        <p className="min-w-[240px] text-right text-xs font-medium text-white/80">
          Designed and developed by TEPL Development team.
        </p>
      </footer>
    </div>
  );
}
