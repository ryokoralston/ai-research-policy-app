import Link from "next/link";
import { Search, FileText, BookOpen, Shield, FlaskConical, Users, Mail, ArrowRight } from "lucide-react";

// Ordered to match the sidebar nav.
const FEATURES = [
  {
    href: "/research",
    icon: Search,
    title: "New Research",
    description: "Search the web and synthesize findings on any AI policy topic",
    color: "blue",
  },
  {
    href: "/reports/new",
    icon: FileText,
    title: "Generate Report",
    description: "Create a congressional briefing, policy memo, or risk assessment",
    color: "purple",
  },
  {
    href: "/library",
    icon: BookOpen,
    title: "Upload Document",
    description: "Index PDFs and papers to search and query with AI",
    color: "green",
  },
  {
    href: "/analysis",
    icon: Shield,
    title: "Risk Analysis",
    description: "Run a structured AI risk assessment on a technology or policy",
    color: "amber",
  },
  {
    href: "/datalab",
    icon: FlaskConical,
    title: "Data Lab",
    description: "Upload a data file and have Claude analyze it in a sandbox, with charts",
    color: "teal",
  },
  {
    href: "/debate",
    icon: Users,
    title: "Policy Debate",
    description: "Simulate a structured debate among 10 expert personas across 4 rounds",
    color: "indigo",
  },
  {
    href: "/digest",
    icon: Mail,
    title: "Daily Digest",
    description: "Automated AI policy news delivery on your schedule",
    color: "cyan",
  },
];

const COLOR_MAP: Record<string, string> = {
  blue:   "bg-blue-600/20 text-blue-400 group-hover:bg-blue-600/30",
  purple: "bg-purple-600/20 text-purple-400 group-hover:bg-purple-600/30",
  green:  "bg-green-600/20 text-green-400 group-hover:bg-green-600/30",
  amber:  "bg-amber-600/20 text-amber-400 group-hover:bg-amber-600/30",
  teal:   "bg-teal-600/20 text-teal-400 group-hover:bg-teal-600/30",
  indigo: "bg-indigo-600/20 text-indigo-400 group-hover:bg-indigo-600/30",
  cyan:   "bg-cyan-600/20 text-cyan-400 group-hover:bg-cyan-600/30",
};

export default function Dashboard() {
  return (
    <div className="p-8 max-w-5xl mx-auto">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-3xl font-bold text-slate-100 mb-2">AI Policy Research Assistant</h1>
        <p className="text-slate-400 text-lg">
          Conduct expert-quality policy research, generate congressional briefings, and analyze AI risks.
        </p>
      </div>

      {/* Feature grid — uniform cards, sidebar order.
          auto-rows-fr equalizes row heights so the last card (alone on its
          row) matches the rest instead of shrinking to fit. Only from sm up:
          in single-column there is nothing to align against, and forcing the
          tallest card's height on every row would just add dead space. */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 sm:auto-rows-fr">
        {FEATURES.map(({ href, icon: Icon, title, description, color }) => (
          <Link
            key={href}
            href={href}
            className="group bg-slate-900 border border-slate-800 rounded-xl p-5 hover:border-slate-700 transition-all"
          >
            <div className="flex items-start gap-4">
              <div className={`p-2.5 rounded-lg transition-colors flex-shrink-0 ${COLOR_MAP[color]}`}>
                <Icon size={20} />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between gap-2 mb-1">
                  <h3 className="text-slate-100 font-semibold">{title}</h3>
                  <ArrowRight
                    size={16}
                    className="text-slate-600 group-hover:text-slate-400 transition-colors flex-shrink-0"
                  />
                </div>
                <p className="text-slate-400 text-sm leading-relaxed">{description}</p>
              </div>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
