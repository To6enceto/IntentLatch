import type { IconName } from "./components/Icon";

export type ConsolePage = {
  path: string;
  title: string;
  description: string;
  group: "Monitor" | "Govern" | "Assure";
  icon: IconName;
};

export const CONSOLE_PAGES: ConsolePage[] = [
  {
    path: "/metrics", title: "Metrics", group: "Monitor", icon: "metrics",
    description: "Gateway requests, policy decisions and response times.",
  },
  {
    path: "/teams", title: "Teams & identities", group: "Govern", icon: "teams",
    description: "Teams, employee identities and authorized models.",
  },
  {
    path: "/policies", title: "Policies", group: "Govern", icon: "policies",
    description: "Authority, limit, regex and AI policies for the gateway.",
  },
  {
    path: "/tests", title: "Test cases", group: "Assure", icon: "tests",
    description: "Policy test cases and their expected decisions.",
  },
  {
    path: "/reports", title: "Reports", group: "Assure", icon: "reports",
    description: "Decision records and gateway activity over a selected time range.",
  },
];
