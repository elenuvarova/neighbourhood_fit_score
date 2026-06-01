import { useState, useEffect } from "react";

function useFetch(url) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(url)
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        return r.json();
      })
      .then(setData)
      .catch(setError)
      .finally(() => setLoading(false));
  }, [url]);

  return { data, error, loading };
}

export default function App() {
  const hello = useFetch("/api/hello");
  const health = useFetch("/api/health");

  return (
    <div className="container">
      <h1>Full-Stack Template</h1>

      <section className="card">
        <h2>/api/hello</h2>
        {hello.loading && <p className="muted">Loading…</p>}
        {hello.error && <p className="error">Error: {hello.error.message}</p>}
        {hello.data && <p>{hello.data.message}</p>}
      </section>

      <section className="card">
        <h2>/api/health</h2>
        {health.loading && <p className="muted">Loading…</p>}
        {health.error && <p className="error">Error: {health.error.message}</p>}
        {health.data && (
          <p>
            Status: <strong>{health.data.status}</strong> &nbsp;|&nbsp; DB:{" "}
            <strong>{health.data.db}</strong>
          </p>
        )}
      </section>
    </div>
  );
}
