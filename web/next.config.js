/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  productionBrowserSourceMaps: false,
  images: { unoptimized: true },
  // Tree-shake big icon/UI libs more aggressively.
  experimental: {
    optimizePackageImports: [
      "lucide-react",
      "framer-motion",
      "recharts",
      "swr",
      "@radix-ui/react-tooltip",
      "@radix-ui/react-dialog",
    ],
  },
};

module.exports = nextConfig;
