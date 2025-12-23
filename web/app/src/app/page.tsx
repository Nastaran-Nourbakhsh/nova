"use client";

import { useEffect, useState } from "react";
import { supabase } from "@/lib/supabaseClient";

export default function Home() {
  const [email, setEmail] = useState("admin@nova.local");
  const [password, setPassword] = useState("Nova123!test");
  const [userEmail, setUserEmail] = useState<string | null>(null);
  const [msg, setMsg] = useState<string>("");

  useEffect(() => {
    supabase.auth.getUser().then(({ data }) => {
      setUserEmail(data.user?.email ?? null);
    });
    const { data: sub } = supabase.auth.onAuthStateChange((_event, session) => {
      setUserEmail(session?.user?.email ?? null);
    });
    return () => sub.subscription.unsubscribe();
  }, []);

  async function signIn() {
    setMsg("");
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) setMsg(error.message);
  }

  async function signOut() {
    await supabase.auth.signOut();
  }

  return (
    <main className="min-h-screen flex items-center justify-center p-6">
      <div className="w-full max-w-md rounded-2xl border p-6 shadow-sm">
        <h1 className="text-2xl font-semibold">Nova Dashboard</h1>
        <p className="text-sm text-gray-500 mt-1">Local dev login</p>

        {userEmail ? (
          <div className="mt-6 space-y-4">
            <div className="text-sm">
              Signed in as <span className="font-medium">{userEmail}</span>
            </div>
            <button
              onClick={() => (window.location.href = "/dashboard")}
              className="w-full rounded-xl border py-2"
            >
              Go to dashboard
            </button>
            <button
              onClick={signOut}
              className="w-full rounded-xl bg-black text-white py-2"
            >
              Sign out
            </button>
            <div className="text-xs text-gray-500">
              Next: jobs list page (F1 continuing)
            </div>
          </div>
        ) : (
          <div className="mt-6 space-y-3">
            <input
              className="w-full rounded-xl border px-3 py-2"
              placeholder="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
            <input
              className="w-full rounded-xl border px-3 py-2"
              placeholder="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <button
              onClick={signIn}
              className="w-full rounded-xl bg-black text-white py-2"
            >
              Sign in
            </button>
            {msg && <div className="text-sm text-red-600">{msg}</div>}
          </div>
        )}
      </div>
    </main>
  );
}

