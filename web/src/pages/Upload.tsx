import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Job } from "../api";
import { useLoad } from "../ui";

const DONE = ["COMPLETED", "DUPLICATE", "FAILED"];

export function Upload() {
  const [file, setFile] = useState<File | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const limits = useLoad(api.siteInfo, []);
  const maxMb = limits.data?.maxUploadMb;
  const tooBig = !!(file && maxMb && file.size > maxMb * 1024 * 1024);

  useEffect(() => {
    if (!job || DONE.includes(job.status)) return;
    const t = setTimeout(() => api.job(job.jobId).then(setJob, (e) => setError(e.message)), 3000);
    return () => clearTimeout(t);
  }, [job]);

  async function send() {
    if (!file || tooBig) return;
    setBusy(true);
    setError(null);
    setJob(null);
    try {
      setJob(await api.upload(file));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1>Upload a demo</h1>
      <p className="muted">
        In CS2, open Watch → Your Matches and download the match. Then pick the <code>.dem</code> file here. The demo
        is deleted from the server after it has been analyzed.{maxMb ? ` Demos up to ${maxMb} MB, one at a time, ${limits.data!.maxUploadsPerDay} per day.` : ""}
      </p>
      <div className="upload">
        <input type="file" accept=".dem" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        <button className="button primary" disabled={!file || busy || tooBig} onClick={send}>
          {busy ? "Uploading…" : "Analyze"}
        </button>
      </div>
      {tooBig && (
        <p className="error">
          This file is {Math.round(file!.size / 1024 / 1024)} MB, more than the {maxMb} MB limit. Is it the right demo?
        </p>
      )}
      {error && <p className="error">{error}</p>}
      {job && (
        <p>
          {job.status === "QUEUED" && "Waiting in line…"}
          {job.status === "PROCESSING" && "Analyzing, this takes about a minute…"}
          {job.status === "FAILED" && <span className="error">Analysis failed: {job.error}</span>}
          {(job.status === "COMPLETED" || job.status === "DUPLICATE") && job.matchId && (
            <>
              {job.status === "DUPLICATE" ? "This match was already analyzed. " : "Done. "}
              <Link to={`/matches/${encodeURIComponent(job.matchId)}`}>Open the match</Link>
            </>
          )}
        </p>
      )}
    </>
  );
}
