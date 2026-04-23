import { NextResponse } from "next/server";

/** Exposes backend config to the client at request time (not build time). */
export async function GET() {
  return NextResponse.json({
    apiBase: process.env.FASTAPI_URL ?? process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000",
    dryRun: process.env.DRY_RUN !== "false",
  });
}
