import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Future: configure API proxy to backend
  // async rewrites() {
  //   return [
  //     {
  //       source: "/api/:path*",
  //       destination: `${process.env.BACKEND_URL}/api/:path*`,
  //     },
  //   ];
  // },
};

export default nextConfig;
