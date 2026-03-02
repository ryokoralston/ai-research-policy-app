import Link from "next/link";
import { Search, FileText, BookOpen, Shield, Users, ArrowRight } from "lucide-react";

const QUICK_STARTS = [
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
];

const COLOR_MAP: Record<string, string> = {
  blue:   "bg-blue-600/20 text-blue-400 group-hover:bg-blue-600/30",
  purple: "bg-purple-600/20 text-purple-400 group-hover:bg-purple-600/30",
  green:  "bg-green-600/20 text-green-400 group-hover:bg-green-600/30",
  amber:  "bg-amber-600/20 text-amber-400 group-hover:bg-amber-600/30",
};

const DEBATE_PERSONAS = [
  { initials: "SC", color: "bg-violet-600" },
  { initials: "MW", color: "bg-blue-600" },
  { initials: "PM", color: "bg-slate-600" },
  { initials: "AO", color: "bg-rose-600" },
  { initials: "HT", color: "bg-teal-600" },
  { initials: "EV", color: "bg-amber-600" },
  { initials: "JC", color: "bg-emerald-600" },
  { initials: "RK", color: "bg-orange-600" },
  { initials: "PP", color: "bg-cyan-600" },
  { initials: "AS", color: "bg-red-600" },
];

export default function Dashboard() {
  return (
    <div className="p-8 max-w-5xl mx-auto">
      {/* Header */}
      <div className="mb-10">
        <h1 className="text-3xl font-bold text-slate-100 mb-2">AI Policy Research Assistant</h1>
        <p className="text-slate-400 text-lg">
          Conduct expert-quality policy research, generate congressional briefings, and analyze AI risks.
        </p>
      </div>

      {/* Quick Start Cards — 2-column grid */}
      <div className="grid grid-cols-2 gap-4 mb-4">
        {QUICK_STARTS.map(({ href, icon: Icon, title, description, color }) => (
          <Link
            key={href}
            href={href}
            className="group bg-slate-900 border border-slate-800 rounded-xl p-6 hover:border-slate-700 transition-all"
          >
            <div className="flex items-start gap-4">
              <div className={`p-3 rounded-lg transition-colors ${COLOR_MAP[color]}`}>
                <Icon size={22} />
              </div>
              <div className="flex-1">
                <div className="flex items-center justify-between mb-1">
                  <h3 className="text-slate-100 font-semibold">{title}</h3>
                  <ArrowRight size={16} className="text-slate-600 group-hover:text-slate-400 transition-colors" />
                </div>
                <p className="text-slate-400 text-sm leading-relaxed">{description}</p>
              </div>
            </div>
          </Link>
        ))}
      </div>

      {/* Debate Card — full width */}
      <Link
        href="/debate"
        className="group block bg-slate-900 border border-slate-800 rounded-xl p-6 hover:border-indigo-700/50 hover:bg-slate-900 transition-all mb-10"
      >
        <div className="flex items-start gap-4">
          <div className="p-3 rounded-lg bg-indigo-600/20 text-indigo-400 group-hover:bg-indigo-600/30 transition-colors flex-shrink-0">
            <Users size={22} />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center justify-between mb-1">
              <h3 className="text-slate-100 font-semibold">Multi-Persona Policy Debate</h3>
              <ArrowRight size={16} className="text-slate-600 group-hover:text-slate-400 transition-colors" />
            </div>
            <p className="text-slate-400 text-sm leading-relaxed mb-3">
              Simulate a structured debate among 10 fictional AI policy expert personas across 4 rounds, with a moderator synthesis.
            </p>
            {/* Persona avatar strip */}
            <div className="flex items-center gap-1.5">
              {DEBATE_PERSONAS.map((p) => (
                <span
                  key={p.initials}
                  className={`w-6 h-6 rounded-full flex items-center justify-center text-[9px] font-bold text-white flex-shrink-0 ${p.color}`}
                >
                  {p.initials}
                </span>
              ))}
              <span className="text-slate-500 text-xs ml-1">10 personas · 4 rounds</span>
            </div>
          </div>
        </div>
      </Link>

      {/* About */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-6">
        <h2 className="text-slate-100 font-semibold mb-3">About This Tool</h2>
        <p className="text-slate-400 text-sm leading-relaxed">
          This research assistant is designed to support AI policy analysis at the level of a senior researcher.
          It combines real-time web research via Tavily, document analysis with RAG, and structured report generation
          using Claude Opus 4. Reports follow structured formats for congressional briefings, policy memos, and risk assessments.
        </p>
        <div className="mt-4 grid grid-cols-3 gap-4">
          {[
            { label: "Add API Keys", desc: "Configure .env with Anthropic + Tavily keys to enable all features" },
            { label: "Upload Documents", desc: "Index PDFs from academic papers, hearings, and policy documents" },
            { label: "Start Researching", desc: "Enter a policy question to begin automated research synthesis" },
          ].map(({ label, desc }) => (
            <div key={label} className="bg-slate-800/50 rounded-lg p-4">
              <p className="text-blue-400 text-xs font-medium mb-1">{label}</p>
              <p className="text-slate-400 text-xs leading-relaxed">{desc}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
