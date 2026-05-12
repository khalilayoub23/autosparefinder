import React, { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { MessageCircle, X, Send, Bot, Loader2 } from 'lucide-react';
import { useChatStore } from '../stores/chatStore';
import { useAuthStore } from '../stores/authStore';
import { useNavigate } from 'react-router-dom';

const NirChatOverlay = () => {
  const [isOpen, setIsOpen] = useState(false);
  const [input, setInput] = useState('');
  const { user } = useAuthStore();
  const navigate = useNavigate();
  const {
    messages,
    isTyping,
    sendMessage,
    loadConversations,
    currentConversationId,
    selectConversation
  } = useChatStore();

  const bottomRef = useRef(null);

  useEffect(() => {
    if (isOpen && user) {
        loadConversations().catch(() => {});
        if (currentConversationId) {
            selectConversation(currentConversationId).catch(() => {});
        }
    }
  }, [isOpen, user]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isTyping]);

  const handleSend = async () => {
    if (!input.trim() || isTyping) return;

    if (!user) {
        navigate('/login');
        return;
    }

    const text = input;
    setInput('');
    try {
      await sendMessage(text);
    } catch (err) {
      console.error(err);
    }
  };

  return (
    <div className="fixed bottom-8 right-8 z-50">
      <AnimatePresence>
        {isOpen && (
          <motion.div
            initial={{ opacity: 0, y: 20, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 20, scale: 0.95 }}
            className="absolute bottom-24 right-0 w-[380px] h-[600px] max-h-[80vh] bg-[#1B2228] border border-white/10 rounded-3xl shadow-2xl overflow-hidden flex flex-col backdrop-blur-xl"
          >
            {/* Header */}
            <div className="p-5 bg-gradient-to-r from-[#00A3FF] to-[#0066FF] flex items-center justify-between shadow-lg">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 bg-white/20 rounded-xl flex items-center justify-center">
                  <Bot className="text-white w-6 h-6" />
                </div>
                <div>
                  <h3 className="font-black text-white leading-none tracking-tight">Nir</h3>
                  <p className="text-[10px] text-white/70 uppercase tracking-[0.2em] mt-1 font-black">AI Assistant</p>
                </div>
              </div>
              <button
                onClick={() => setIsOpen(false)}
                className="p-2 hover:bg-white/10 rounded-full transition-colors text-white"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Messages */}
            <div className="flex-1 overflow-y-auto p-6 space-y-4 scrollbar-thin scrollbar-thumb-white/10">
              {!user ? (
                <div className="h-full flex flex-col items-center justify-center text-center px-6">
                  <div className="w-16 h-16 bg-white/5 rounded-2xl flex items-center justify-center mb-4">
                    <Bot className="w-8 h-8 text-[#00CCFF]" />
                  </div>
                  <p className="text-white font-bold mb-2">Welcome to AutoSpareFinder</p>
                  <p className="text-gray-400 text-sm mb-6">
                    Please login to start a conversation with Nir.
                  </p>
                  <button
                    onClick={() => navigate('/login')}
                    className="w-full py-3 bg-[#00CCFF] text-[#0A0F14] font-black rounded-xl hover:bg-white transition-colors"
                  >
                    Login to Chat
                  </button>
                </div>
              ) : messages.length === 0 ? (
                <div className="h-full flex flex-col items-center justify-center text-center px-6">
                  <div className="w-16 h-16 bg-white/5 rounded-2xl flex items-center justify-center mb-4">
                    <MessageCircle className="w-8 h-8 text-[#00CCFF]" />
                  </div>
                  <p className="text-gray-400 text-sm font-medium">
                    Hello {user.full_name?.split(' ')[0]}! I'm Nir. How can I help you find the right parts today?
                  </p>
                </div>
              ) : (
                messages.map((m) => (
                  <div key={m.id} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                    <div className={`max-w-[85%] p-4 rounded-2xl text-sm leading-relaxed ${
                      m.role === 'user'
                        ? 'bg-[#00A3FF] text-white rounded-tr-sm shadow-lg'
                        : 'bg-white/5 text-gray-200 border border-white/10 rounded-tl-sm'
                    }`}>
                      {m.content}
                    </div>
                  </div>
                ))
              )}
              {isTyping && (
                <div className="flex justify-start">
                  <div className="bg-white/5 border border-white/10 p-4 rounded-2xl rounded-tl-sm flex gap-1.5">
                    <span className="w-1.5 h-1.5 bg-[#00CCFF] rounded-full animate-bounce" />
                    <span className="w-1.5 h-1.5 bg-[#00CCFF] rounded-full animate-bounce [animation-delay:0.2s]" />
                    <span className="w-1.5 h-1.5 bg-[#00CCFF] rounded-full animate-bounce [animation-delay:0.4s]" />
                  </div>
                </div>
              )}
              <div ref={bottomRef} />
            </div>

            {/* Input */}
            <div className="p-6 border-t border-white/5 bg-[#1B2228]/50">
              <div className="relative flex items-center gap-3">
                <input
                  type="text"
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleSend()}
                  placeholder="Type a message..."
                  className="flex-1 bg-white/5 border border-white/10 rounded-xl px-5 py-3.5 text-sm focus:outline-none focus:border-[#00CCFF]/50 transition-colors text-white placeholder:text-gray-600"
                />
                <button
                  onClick={handleSend}
                  disabled={(!input.trim() && user) || isTyping}
                  className="p-3.5 bg-[#00CCFF] text-[#0A0F14] rounded-xl hover:bg-white transition-colors disabled:opacity-30 flex items-center justify-center"
                >
                  {isTyping ? <Loader2 className="w-5 h-5 animate-spin" /> : <Send className="w-5 h-5" />}
                </button>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <motion.button
        whileHover={{ scale: 1.05 }}
        whileTap={{ scale: 0.95 }}
        onClick={() => setIsOpen(!isOpen)}
        className={`group flex items-center gap-5 bg-[#1B2228]/90 hover:bg-[#1B2228] border border-white/10 p-2.5 pr-8 rounded-full transition-all backdrop-blur-xl shadow-[0_20px_50px_rgba(0,0,0,0.5)] ${isOpen ? 'opacity-0 scale-0' : 'opacity-100 scale-100'}`}
      >
        <div className="w-14 h-14 bg-gradient-to-tr from-[#00CCFF] to-[#0066FF] rounded-full flex items-center justify-center shadow-[0_0_30px_rgba(0,163,255,0.4)] group-hover:scale-110 transition-transform duration-300">
          <MessageCircle className="text-white w-7 h-7" />
        </div>
        <div className="text-right">
          <p className="text-[10px] text-[#00CCFF] uppercase tracking-[0.2em] font-black mb-0.5">Assistant</p>
          <p className="text-lg font-black tracking-tight text-white leading-none">Talk to Nir</p>
        </div>
      </motion.button>
    </div>
  );
};

export default NirChatOverlay;
