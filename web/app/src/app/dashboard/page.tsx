"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabaseClient";
import Link from "next/link";

type Org = { id: string; slug: string; name: string };
type Job = { id: string; created_at: string; status: string };

export default function DashboardPage() {
  const router = useRouter();
  const [email, setEmail] = useState<string>("");
  const [org, setOrg] = useState<Org | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");

  useEffect(() => {
    (async () => {
      setLoading(true);
      setErr("");

      const { data: userRes } = await supabase.auth.getUser();
      const user = userRes.user;
      if (!user) {
        router.push("/");
        return;
      }
      setEmail(user.email ?? "");

      // 1) Find this user's org membership (take first org for v1)
      const { data: memberRows, error: memberErr } = await supabase
        .from("org_members")
        .select("role, orgs:org_id (id, slug, name)")
        .limit(1);

      if (memberErr) {
        setErr(`org_members error: ${memberErr.message}`);
        setLoading(false);
        return;
      }

      const first: any = memberRows?.[0];
      const o: Org | undefined = first?.orgs;
      if (!o) {
        setErr("No org membership found for this user.");
        setLoading(false);
        return;
      }
      setOrg(o);

      // 2) Load recent jobs for that org
      const { data: jobRows, error: jobsErr } = await supabase
        .from("jobs")
        .select("id, created_at, status")
        .eq("org_id", o.id)
        .order("created_at", { ascending: false })
        .limit(20);

      if (jobsErr) {
        setErr(`jobs error: ${jobsErr.message}`);
        setLoading(false);
        return;
      }

      setJobs((jobRows ?? []) as Job[]);
      setLoading(false);
    })();
  }, [router]);

  async function signOut() {
    await supabase.auth.signOut();
    router.push("/");
  }

  return (
    <main className="min-h-screen p-6">
      <div className="mx-auto max-w-4xl space-y-6">
        <div className="rounded-2xl border p-5 shadow-sm flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold">Dashboard</h1>
            <div className="mt-1 text-sm text-gray-500">Signed in as {email}</div>
            <div className="mt-1 text-sm">
              Org:{" "}
              <span className="font-medium">
                {org ? `${org.name} (${org.slug})` : "—"}
              </span>
            </div>
          </div>
          <button
            onClick={signOut}
            className="rounded-xl bg-black px-4 py-2 text-white"
          >
            Sign out
          </button>
        </div>

        <div className="rounded-2xl border p-5 shadow-sm">
          <h2 className="text-lg font-semibold">Recent jobs</h2>

          {loading && <div className="mt-3 text-sm text-gray-500">Loading…</div>}
          {err && <div className="mt-3 text-sm text-red-600">{err}</div>}

          {!loading && !err && (
            <div className="mt-3 overflow-hidden rounded-xl border">
              {jobs.length === 0 ? (
                <div className="p-4 text-sm text-gray-500">
                  No jobs yet. Ingest a scan via the API to create one.
                </div>
              ) : (
                <table className="w-full text-sm">
                  <thead className="bg-gray-50">
                    <tr>
                      <th className="px-3 py-2 text-left">Job ID</th>
                      <th className="px-3 py-2 text-left">Status</th>
                      <th className="px-3 py-2 text-left">Created</th>
                    </tr>
                  </thead>
                  <tbody>
                    {jobs.map((j) => (
                      <tr key={j.id} className="border-t">
                        <td className="px-3 py-2 font-mono text-xs"><Link className="underline" href={`/jobs/${j.id}`}>{j.id}</Link></td>
                        <td className="px-3 py-2">{j.status}</td>
                        <td className="px-3 py-2">
                          {new Date(j.created_at).toLocaleString()}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}
        </div>
      </div>
    </main>
  );
}

