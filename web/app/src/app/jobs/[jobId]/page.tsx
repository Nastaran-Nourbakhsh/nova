"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { supabase } from "@/lib/supabaseClient";

type Job = {
  id: string;
  org_id: string;
  status: string;
  created_at: string;
};

type Ring = {
  id: string;
  job_id: string;
  ring_label: string;
};

type Diamond = {
  id: string;
  job_id: string;
  ring_id: string;
  slot_index: number;
  captured_at: string;
};

type DiamondImage = {
  id: string;
  diamond_id: string;
  image_type: "UV_FREE" | "ASET";
  // MUST be path relative to the bucket, e.g.:
  // first-customer/<jobId>/A/slot_0_uv_free.jpg
  storage_path: string;
  preview_storage_path: string | null;
  preview_ready: boolean;
  created_at: string;
};

function toThumbPath(originalPath: string) {
  // "a/b/c.jpg" -> "a/b/c_thumb.jpg"
  const idx = originalPath.lastIndexOf(".");
  if (idx === -1) return originalPath + "_thumb"; // fallback
  return originalPath.slice(0, idx) + "_thumb" + originalPath.slice(idx);
}

function getPreviewPath(img?: DiamondImage | null) {
  if (!img) return "";
  return img.preview_storage_path ?? toThumbPath(img.storage_path);
}

export default function JobDetailPage() {
  // ---- Settings ----
  const POLL_MS = 3000; // 3 seconds
  const SIGNED_URL_TTL_SEC = 60; // thumbnails only; refreshed as needed
  const BUCKET = "diamond-previews";

  const router = useRouter();
  const params = useParams();
  const jobId = params?.jobId as string;

  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");

  const [job, setJob] = useState<Job | null>(null);
  const [rings, setRings] = useState<Ring[]>([]);
  const [diamonds, setDiamonds] = useState<Diamond[]>([]);
  const [images, setImages] = useState<DiamondImage[]>([]);

  // Header UX
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [newDiamondsDelta, setNewDiamondsDelta] = useState<number>(0);

  // Prevent overlapping polls under slow network
  const inFlightRef = useRef(false);

  // Track delta between polls
  const prevDiamondCountRef = useRef<number>(0);

  // Cache signed download URLs: storage_path -> signedUrl
  const [signedUrlByPath, setSignedUrlByPath] = useState<Record<string, string>>(
    {}
  );

  // IMPORTANT: keep a ref mirror so polling effect does NOT depend on the cache
  const signedUrlByPathRef = useRef<Record<string, string>>({});
  useEffect(() => {
    signedUrlByPathRef.current = signedUrlByPath;
  }, [signedUrlByPath]);

  // Avoid concurrent signing bursts
  const signingRef = useRef(false);

  // ---- Groupings for UI ----
  const imagesByDiamond = useMemo(() => {
    const m = new Map<string, DiamondImage[]>();
    for (const img of images) {
      const arr = m.get(img.diamond_id) ?? [];
      arr.push(img);
      m.set(img.diamond_id, arr);
    }
    // stable ordering UV then ASET
    for (const [k, arr] of m.entries()) {
      arr.sort((a, b) => a.image_type.localeCompare(b.image_type));
      m.set(k, arr);
    }
    return m;
  }, [images]);

  const diamondsByRing = useMemo(() => {
    const m = new Map<string, Diamond[]>();
    for (const d of diamonds) {
      const arr = m.get(d.ring_id) ?? [];
      arr.push(d);
      m.set(d.ring_id, arr);
    }
    // sort by slot_index
    for (const [k, arr] of m.entries()) {
      arr.sort((a, b) => a.slot_index - b.slot_index);
      m.set(k, arr);
    }
    return m;
  }, [diamonds]);

  async function ensureSignedUrlsFor(paths: string[]) {
    if (signingRef.current) return;
    signingRef.current = true;

    try {
      const unique = Array.from(new Set(paths)).filter((p) => !!p);

      // Use ref cache (NOT state) to avoid stale and avoid effect dependency loop
      const cache = signedUrlByPathRef.current;
      const toSign = unique.filter((p) => !cache[p]);

      if (toSign.length === 0) return;

      const results = await Promise.all(
        toSign.map(async (path) => {
          const { data, error } = await supabase.storage
            .from(BUCKET)
            .createSignedUrl(path, SIGNED_URL_TTL_SEC);
          console.log("SIGNED URL DEBUG", {
            bucket: BUCKET,
            path,
            signedUrl: data?.signedUrl ?? null,
            error: error ? { message: error.message, name: (error as any).name } : null,
          });
//           if (error) {
//             console.log("createSignedUrl error:", path, error.message);
//             return { path, signedUrl: "" };
//           }
          if (!data?.signedUrl) return { path, signedUrl: "" };
          return { path, signedUrl: data.signedUrl };
        })
      );

      setSignedUrlByPath((prev) => {
        const next = { ...prev };
        for (const r of results) {
          if (r.signedUrl) next[r.path] = r.signedUrl;
        }
        return next;
      });
    } finally {
      signingRef.current = false;
    }
  }

  // ---- Main loader + polling ----
  useEffect(() => {
    let cancelled = false;
    let timer: any = null;

    async function loadOnce(isFirst: boolean) {
      if (cancelled) return;

      // prevent overlapping polls
      if (!isFirst && inFlightRef.current) return;
      inFlightRef.current = true;

      try {
        if (isFirst) {
          setLoading(true);
          setErr("");
        } else {
          setErr("");
        }

        // Ensure logged in
        const { data: userRes } = await supabase.auth.getUser();
        if (!userRes.user) {
          router.push("/");
          return;
        }

        // 1) Load job
        const { data: jobRow, error: jobErr } = await supabase
          .from("jobs")
          .select("id, org_id, status, created_at")
          .eq("id", jobId)
          .maybeSingle();

        if (jobErr) {
          if (!cancelled) setErr(`job error: ${jobErr.message}`);
          return;
        }
        if (!jobRow) {
          if (!cancelled) setErr("Job not found (or you don't have access).");
          return;
        }
        if (!cancelled) setJob(jobRow as Job);

        // 2) Load rings
        const { data: ringRows, error: ringErr } = await supabase
          .from("rings")
          .select("id, job_id, ring_label")
          .eq("job_id", jobId)
          .order("ring_label", { ascending: true });

        if (ringErr) {
          if (!cancelled) setErr(`rings error: ${ringErr.message}`);
          return;
        }
        if (!cancelled) setRings((ringRows ?? []) as Ring[]);

        // 3) Load diamonds
        const { data: diamondRows, error: diamondErr } = await supabase
          .from("diamonds")
          .select("id, job_id, ring_id, slot_index, captured_at")
          .eq("job_id", jobId)
          .order("slot_index", { ascending: true });

        if (diamondErr) {
          if (!cancelled) setErr(`diamonds error: ${diamondErr.message}`);
          return;
        }

        const ds = (diamondRows ?? []) as Diamond[];

        // UX: show delta since last refresh
        const prevCount = prevDiamondCountRef.current;
        const nextCount = ds.length;
        const delta = Math.max(0, nextCount - prevCount);
        prevDiamondCountRef.current = nextCount;

        if (!cancelled) {
          setNewDiamondsDelta(delta);
          setDiamonds(ds);
        }

        // 4) Load images
        const diamondIds = ds.map((d) => d.id);
        if (diamondIds.length === 0) {
          if (!cancelled) setImages([]);
          if (!cancelled) setLastUpdated(new Date());
          return;
        }

        const { data: imageRows, error: imageErr } = await supabase
          .from("diamond_images")
          .select("id, diamond_id, image_type, storage_path, preview_storage_path, preview_ready, created_at")
          .in("diamond_id", diamondIds);

        if (imageErr) {
          if (!cancelled) setErr(`diamond_images error: ${imageErr.message}`);
          return;
        }

        const imgs = (imageRows ?? []) as DiamondImage[];
        if (!cancelled) setImages(imgs);

        const previewPathsToSign = imgs
          .filter((i) => i.preview_ready) // IMPORTANT
          .map((i) => getPreviewPath(i))
          .filter(Boolean);

        await ensureSignedUrlsFor(previewPathsToSign);



        if (!cancelled) setLastUpdated(new Date());
      } finally {
        inFlightRef.current = false;
        if (!cancelled && isFirst) setLoading(false);
      }
    }

    // initial load
    loadOnce(true);

    // poll loop
    timer = setInterval(() => {
      loadOnce(false);
    }, POLL_MS);

    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
    };
  }, [jobId, router]); // ✅ IMPORTANT: do NOT depend on signedUrlByPath

  const totalDiamonds = diamonds.length;

  return (
    <main className="min-h-screen p-6">
      <div className="mx-auto max-w-6xl space-y-6">
        <div className="flex items-center justify-between">
          <div className="space-y-1">
            <div className="text-sm text-gray-500">
              <Link className="underline" href="/dashboard">
                ← Back to dashboard
              </Link>
            </div>
            <h1 className="text-2xl font-semibold">Job details</h1>
            <div className="text-sm text-gray-600 font-mono">{jobId}</div>

            <div className="flex items-center gap-3">
              {lastUpdated && (
                <div className="text-xs text-gray-500">
                  Updated: {lastUpdated.toLocaleTimeString()}
                </div>
              )}
              {newDiamondsDelta > 0 && (
                <div className="text-xs rounded-full border px-2 py-0.5">
                  +{newDiamondsDelta} new
                </div>
              )}
            </div>
          </div>
        </div>

        {loading && <div className="text-sm text-gray-500">Loading…</div>}
        {err && <div className="text-sm text-red-600">{err}</div>}

        {!loading && !err && job && (
          <>
            <div className="rounded-2xl border p-5 shadow-sm">
              <div className="flex flex-wrap gap-6 text-sm">
                <div>
                  <div className="text-gray-500">Status</div>
                  <div className="font-medium">{job.status}</div>
                </div>
                <div>
                  <div className="text-gray-500">Created</div>
                  <div className="font-medium">
                    {new Date(job.created_at).toLocaleString()}
                  </div>
                </div>
                <div>
                  <div className="text-gray-500">Total diamonds</div>
                  <div className="font-medium">{totalDiamonds}</div>
                </div>
                <div>
                  <div className="text-gray-500">Rings</div>
                  <div className="font-medium">{rings.length}</div>
                </div>
              </div>
            </div>

            <div className="rounded-2xl border p-5 shadow-sm space-y-4">
              <h2 className="text-lg font-semibold">Scan list</h2>

              {rings.length === 0 ? (
                <div className="text-sm text-gray-500">
                  No rings yet. Ingest scans to create rings/diamonds.
                </div>
              ) : (
                <div className="space-y-6">
                  {rings.map((r) => {
                    const ds = diamondsByRing.get(r.id) ?? [];
                    return (
                      <div key={r.id} className="rounded-xl border p-4">
                        <div className="flex items-center justify-between">
                          <div className="font-medium">Ring {r.ring_label}</div>
                          <div className="text-sm text-gray-500">
                            {ds.length} diamonds
                          </div>
                        </div>

                        {ds.length === 0 ? (
                          <div className="mt-3 text-sm text-gray-500">
                            No diamonds yet for this ring.
                          </div>
                        ) : (
                          <div className="mt-3 overflow-hidden rounded-xl border">
                            <table className="w-full text-sm">
                              <thead className="bg-gray-50">
                                <tr>
                                  <th className="px-3 py-2 text-left">Slot</th>
                                  <th className="px-3 py-2 text-left">Diamond ID</th>
                                  <th className="px-3 py-2 text-left">UV</th>
                                  <th className="px-3 py-2 text-left">ASET</th>
                                </tr>
                              </thead>
                              <tbody>
                                {ds.map((d) => {
                                  const imgs = imagesByDiamond.get(d.id) ?? [];
                                  const uv = imgs.find(
                                    (i) => i.image_type === "UV_FREE"
                                  );
                                  const aset = imgs.find(
                                    (i) => i.image_type === "ASET"
                                  );

                                  const uvPreviewPath = getPreviewPath(uv);
                                  const asetPreviewPath = getPreviewPath(aset);

                                  const uvUrl = uvPreviewPath ? signedUrlByPath[uvPreviewPath] : "";
                                  const asetUrl = asetPreviewPath ? signedUrlByPath[asetPreviewPath] : "";


                                  return (
                                    <tr key={d.id} className="border-t">
                                      <td className="px-3 py-2">{d.slot_index}</td>

                                      <td className="px-3 py-2 font-mono text-xs">
                                        {d.id}
                                        <div className="text-[11px] text-gray-500">
                                          captured:{" "}
                                          {d.captured_at
                                            ? new Date(d.captured_at).toLocaleTimeString()
                                            : "—"}
                                        </div>
                                      </td>

                                      <td className="px-3 py-2">
                                        <div className="flex items-center gap-3">
                                          {uv?.preview_ready && uvUrl ? (
                                            <img
                                              src={uvUrl}
                                              alt="UV thumbnail"
                                              className="h-14 w-14 rounded-md border object-cover"
                                            />
                                          ) : (
                                            <div className="h-14 w-14 rounded-md border bg-gray-50 flex items-center justify-center">
                                              <div className="h-5 w-5 animate-spin rounded-full border-2 border-gray-300 border-t-gray-600" />
                                            </div>
                                          )}
                                          <div className="font-mono text-[11px] text-gray-600 break-all">
                                            {uv?.storage_path ?? "—"}
                                          </div>
                                        </div>
                                      </td>

                                      <td className="px-3 py-2">
                                        <div className="flex items-center gap-3">
                                          {asetUrl ? (
                                            <img
                                              src={asetUrl}
                                              alt="ASET thumbnail"
                                              className="h-14 w-14 rounded-md border object-cover"
                                            />
                                          ) : (
                                            <div className="h-14 w-14 rounded-md border bg-gray-50 flex items-center justify-center">
                                              <div className="h-5 w-5 animate-spin rounded-full border-2 border-gray-300 border-t-gray-600" />
                                            </div>
                                          )}
                                          <div className="font-mono text-[11px] text-gray-600 break-all">
                                            {aset?.storage_path ?? "—"}
                                          </div>
                                        </div>
                                      </td>
                                    </tr>
                                  );
                                })}
                              </tbody>
                            </table>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </main>
  );
}
