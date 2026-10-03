// A fixed server-side destination keeps browser requests same-origin, including SSE.
export const dynamic = "force-dynamic";
const allowed =
  /^(health|repositories(?:\/[\w-]+(?:\/(?:index|snapshots))?)?|index-jobs\/[\w-]+|snapshots\/[\w-]+(?:\/(?:tree|files|conversations))?|conversations\/[\w-]+(?:\/messages)?|runs\/[\w-]+(?:\/(?:events|cancel))?)$/;
async function proxy(
  request: Request,
  context: { params: Promise<{ path: string[] }> },
) {
  const { path } = await context.params;
  const endpoint = path.join("/");
  if (!allowed.test(endpoint))
    return Response.json(
      { error: { message: "Endpoint not found." } },
      { status: 404 },
    );
  if (
    ["POST", "DELETE"].includes(request.method) &&
    request.headers.get("origin") &&
    new URL(request.headers.get("origin")!).host !== request.headers.get("host")
  ) {
    return Response.json(
      { error: { message: "Cross-origin request rejected." } },
      { status: 403 },
    );
  }
  try {
    const url = new URL(
      endpoint,
      (process.env.COPILOT_API_URL || "http://127.0.0.1:8000").replace(
        /\/$/,
        "",
      ) + "/",
    );
    url.search = new URL(request.url).search;
    const headers = new Headers({
      Accept: request.headers.get("accept") || "application/json",
    });
    for (const key of ["content-type", "idempotency-key", "last-event-id"]) {
      const value = request.headers.get(key);
      if (value) headers.set(key, value);
    }
    const upstream = await fetch(url, {
      method: request.method,
      headers,
      cache: "no-store",
      redirect: "error",
      body: request.method === "POST" ? await request.text() : undefined,
      signal: request.signal,
    });
    const outgoing = new Headers({
      "Cache-Control": "no-store",
      "X-Accel-Buffering": "no",
    });
    outgoing.set(
      "Content-Type",
      upstream.headers.get("content-type") || "application/json",
    );
    return new Response(upstream.body, {
      status: upstream.status,
      headers: outgoing,
    });
  } catch {
    return Response.json(
      {
        error: {
          message:
            "Cannot reach the repository service. Check that the API is running, then retry.",
        },
      },
      { status: 502 },
    );
  }
}
export { proxy as GET, proxy as POST, proxy as DELETE };
