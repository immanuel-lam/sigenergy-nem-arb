"use client";

import { motion } from "framer-motion";
import { Zap } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { apiPost } from "@/lib/api";

type SpikeRequest = {
  magnitude_c_kwh: number;
  channel: "import" | "export";
  minutes_ahead?: number;
  duration_min?: number;
};

export function SpikeDemoButton() {
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function onClick() {
    setLoading(true);
    setErr(null);
    try {
      const body: SpikeRequest = {
        magnitude_c_kwh: 120,
        channel: "export",
      };
      const result = await apiPost<unknown>("/spike-demo", body);

      // Let the signature animation component listen and take over.
      window.dispatchEvent(
        new CustomEvent("spike-demo", { detail: result }),
      );

      // Scroll the ReplanMoment placeholder into view.
      const target = document.querySelector("[data-replan-moment]");
      if (target) {
        target.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <motion.div
        whileHover={{ scale: loading ? 1 : 1.02 }}
        whileTap={{ scale: 0.98 }}
      >
        <Button
          variant="primary"
          size="md"
          onClick={onClick}
          disabled={loading}
          className="gap-2"
        >
          <Zap className="h-3.5 w-3.5" />
          {loading ? "Injecting…" : "Inject synthetic spike"}
          <span aria-hidden className="text-ink-faint">
            →
          </span>
        </Button>
      </motion.div>
      {err && (
        <span className="text-11 text-rose">
          {err}
        </span>
      )}
    </div>
  );
}
