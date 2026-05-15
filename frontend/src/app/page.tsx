"use client";

import { useState, useRef, useEffect } from "react";

type Message = {
  role: "user" | "assistant";
  content: string;
};

type Source = {
  title: string;
  url: string;
  content: string;
  score: number;
};

type ChatResponse = {
  answer: string;
  sources: Source[];
  is_escalated: boolean;
  ticket_id?: string | null;
};

// --- Icons ---
const SendIcon = () => (
  <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <line x1="22" y1="2" x2="11" y2="13"></line>
    <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
  </svg>
);

const UserIcon = () => (
  <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
    <circle cx="12" cy="7" r="4"></circle>
  </svg>
);

const BotIcon = () => (
  <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <rect x="3" y="11" width="18" height="10" rx="2"></rect>
    <circle cx="12" cy="5" r="2"></circle>
    <path d="M12 7v4"></path>
    <line x1="8" y1="16" x2="8" y2="16"></line>
    <line x1="16" y1="16" x2="16" y2="16"></line>
  </svg>
);

const LinkIcon = () => (
  <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="inline mr-1">
    <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"></path>
    <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"></path>
  </svg>
);

const SunIcon = () => (
  <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="4"></circle>
    <path d="M12 2v2"></path>
    <path d="M12 20v2"></path>
    <path d="M4.93 4.93l1.41 1.41"></path>
    <path d="M17.66 17.66l1.41 1.41"></path>
    <path d="M2 12h2"></path>
    <path d="M20 12h2"></path>
    <path d="M6.34 17.66l-1.41 1.41"></path>
    <path d="M19.07 4.93l-1.41 1.41"></path>
  </svg>
);

const MoonIcon = () => (
  <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"></path>
  </svg>
);

const MessageSquareIcon = () => (
  <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="opacity-70">
    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
  </svg>
);

export default function Home() {
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<Message[]>([
    { role: "assistant", content: "Bonjour ! Je suis l'assistant AI officiel pour Apache Airflow. Comment puis-je vous aider aujourd'hui ?" }
  ]);
  const [isLoading, setIsLoading] = useState(false);
  const [lastSources, setLastSources] = useState<Source[]>([]);
  const [isDarkMode, setIsDarkMode] = useState(true);
  
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Historique des questions posées par l'utilisateur
  const userHistory = messages.filter(m => m.role === "user").map(m => m.content);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, isLoading]);

  // Theme toggle logic
  useEffect(() => {
    if (isDarkMode) {
      document.documentElement.classList.add('dark');
    } else {
      document.documentElement.classList.remove('dark');
    }
  }, [isDarkMode]);

  const toggleTheme = () => setIsDarkMode(!isDarkMode);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isLoading) return;

    const userMessage = input.trim();
    setInput("");
    
    const updatedMessages: Message[] = [...messages, { role: "user", content: userMessage }];
    setMessages(updatedMessages);
    setIsLoading(true);
    setLastSources([]);

    try {
      const response = await fetch("http://127.0.0.1:8000/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: updatedMessages,
          user_email: "demo@user.com"
        }),
      });

      if (!response.ok) throw new Error("Erreur réseau");

      const data: ChatResponse = await response.json();
      
      setMessages((prev) => [...prev, { role: "assistant", content: data.answer }]);
      
      const uniqueSources = data.sources.filter((s, index, self) => 
        index === self.findIndex((t) => t.title === s.title)
      );
      setLastSources(uniqueSources);

    } catch (error) {
      console.error(error);
      setMessages((prev) => [...prev, { role: "assistant", content: "Désolé, une erreur de connexion au serveur s'est produite." }]);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <main className="min-h-screen flex items-center justify-center p-4 sm:p-8 relative">
      
      {/* Theme Toggle Button - Floating top right */}
      <button 
        onClick={toggleTheme}
        className="absolute top-6 right-6 z-50 p-3 rounded-full glass-panel hover:bg-[var(--primary-glow)] transition-all duration-300"
        title="Toggle Theme"
      >
        {isDarkMode ? <SunIcon /> : <MoonIcon />}
      </button>

      <div className="w-full max-w-6xl h-[88vh] flex glass-panel rounded-3xl overflow-hidden relative z-10 shadow-2xl shadow-blue-500/10">
        
        {/* Sidebar - History */}
        <div className="hidden md:flex flex-col w-72 sidebar-panel">
          <div className="p-6 border-b" style={{ borderColor: 'var(--surface-border)' }}>
            <h2 className="text-sm font-semibold tracking-wider uppercase opacity-80 flex items-center gap-2">
              <MessageSquareIcon />
              Historique
            </h2>
          </div>
          <div className="flex-1 overflow-y-auto p-4 space-y-2">
            {userHistory.length === 0 ? (
              <p className="text-xs opacity-50 text-center mt-10">Aucune question posée</p>
            ) : (
              userHistory.map((query, idx) => (
                <div key={idx} className="p-3 text-sm rounded-xl cursor-default transition-all duration-200 hover:bg-[var(--primary-glow)]" style={{ background: 'var(--surface)', border: '1px solid var(--surface-border)' }}>
                  <p className="line-clamp-2 opacity-90">{query}</p>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Main Chat Area */}
        <div className="flex-1 flex flex-col h-full relative">
          {/* Header */}
          <div className="flex-none p-5 lg:p-6 border-b flex items-center justify-between" style={{ borderColor: 'var(--surface-border)', background: 'var(--surface)' }}>
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 rounded-full bg-gradient-to-tr from-blue-500 to-purple-500 flex items-center justify-center shadow-lg shadow-blue-500/20 text-white">
                <BotIcon />
              </div>
              <div>
                <h1 className="text-xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-blue-500 to-purple-500 tracking-tight">
                  Airflow Support AI
                </h1>
                <p className="text-xs opacity-60 flex items-center gap-2 mt-0.5 font-medium">
                  <span className="w-2 h-2 rounded-full bg-green-500 inline-block animate-pulse"></span>
                  Propulsé par RAG & Cohere
                </p>
              </div>
            </div>
          </div>

          {/* Messages */}
          <div className="flex-1 overflow-y-auto p-4 lg:p-8 space-y-8">
            {messages.map((msg, idx) => {
              const isUser = msg.role === "user";
              return (
                <div key={idx} className={`flex gap-4 ${isUser ? "flex-row-reverse" : "flex-row"} message-appear`} style={{ animationDelay: '0.1s' }}>
                  
                  {/* Avatar */}
                  <div 
                    className={`w-10 h-10 rounded-full flex items-center justify-center flex-shrink-0 shadow-md text-white`}
                    style={{ background: isUser ? 'var(--user-msg-bg)' : 'var(--bot-msg-bg)', color: isUser ? '#fff' : 'var(--foreground)' }}
                  >
                    {isUser ? <UserIcon /> : <BotIcon />}
                  </div>

                  {/* Message Bubble */}
                  <div className={`max-w-[85%] lg:max-w-[75%] flex flex-col ${isUser ? "items-end" : "items-start"}`}>
                    <div 
                      className={`px-5 py-4 rounded-2xl text-[15px] leading-relaxed shadow-sm`}
                      style={{ 
                        background: isUser ? 'var(--user-msg-bg)' : 'var(--bot-msg-bg)',
                        color: isUser ? 'var(--user-msg-text)' : 'var(--bot-msg-text)',
                        border: isUser ? 'none' : `1px solid var(--bot-msg-border)`,
                        borderTopRightRadius: isUser ? '4px' : '1rem',
                        borderTopLeftRadius: !isUser ? '4px' : '1rem'
                      }}
                    >
                      {msg.content.split('\n').map((line, i) => (
                        <span key={i}>
                          {line}
                          <br />
                        </span>
                      ))}
                    </div>
                    
                    {/* Sources (only for the last bot message) */}
                    {!isUser && idx === messages.length - 1 && lastSources.length > 0 && (
                      <div className="mt-3 flex flex-wrap gap-2 w-full">
                        {lastSources.map((source, sIdx) => (
                          <a 
                            key={sIdx}
                            href={source.url}
                            target="_blank"
                            rel="noreferrer"
                            className="flex items-center text-[11px] font-medium px-3 py-1.5 rounded-full transition-all duration-200 opacity-80 hover:opacity-100"
                            style={{ background: 'var(--surface)', border: '1px solid var(--primary-glow)', color: 'var(--primary)' }}
                          >
                            <LinkIcon />
                            <span className="truncate max-w-[200px]">{source.title}</span>
                          </a>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}

            {/* Typing Indicator */}
            {isLoading && (
              <div className="flex gap-4 flex-row message-appear">
                <div className="w-10 h-10 rounded-full flex items-center justify-center flex-shrink-0" style={{ background: 'var(--bot-msg-bg)' }}>
                  <BotIcon />
                </div>
                <div className="px-5 py-5 rounded-2xl rounded-tl-sm flex items-center gap-1.5" style={{ background: 'var(--bot-msg-bg)', border: `1px solid var(--bot-msg-border)` }}>
                  <div className="w-2 h-2 rounded-full opacity-60 typing-dot" style={{ background: 'var(--foreground)' }}></div>
                  <div className="w-2 h-2 rounded-full opacity-60 typing-dot" style={{ background: 'var(--foreground)' }}></div>
                  <div className="w-2 h-2 rounded-full opacity-60 typing-dot" style={{ background: 'var(--foreground)' }}></div>
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>

          {/* Input Area */}
          <div className="p-4 lg:p-6" style={{ background: 'var(--surface)', borderTop: '1px solid var(--surface-border)' }}>
            <form onSubmit={handleSubmit} className="relative flex items-center max-w-4xl mx-auto">
              <input
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Posez votre question sur Apache Airflow..."
                disabled={isLoading}
                className="w-full rounded-full pl-6 pr-16 py-4 focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all shadow-sm text-[15px]"
                style={{ 
                  background: 'var(--input-bg)', 
                  border: '1px solid var(--input-border)',
                  color: 'var(--input-text)'
                }}
              />
              <button
                type="submit"
                disabled={!input.trim() || isLoading}
                className="absolute right-2 w-10 h-10 rounded-full bg-blue-600 hover:bg-blue-500 flex items-center justify-center text-white disabled:opacity-50 disabled:cursor-not-allowed transition-colors shadow-md"
              >
                <SendIcon />
              </button>
            </form>
            <div className="text-center mt-3 text-[10px] uppercase tracking-widest font-semibold opacity-40">
              Airflow Knowledge Base • Cohere ReRank
            </div>
          </div>
        </div>

      </div>
    </main>
  );
}
