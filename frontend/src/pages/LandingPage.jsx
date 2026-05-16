import React, { useEffect } from 'react';
import { motion } from 'framer-motion';
import { useNavigate, Link } from 'react-router-dom';
import { Search, LogIn } from 'lucide-react';
import BrandLogo from '../components/BrandLogo';
import NirChatOverlay from '../components/NirChatOverlay';

const FloatingPart = ({ children, delay = 0, duration = 5, x = 0, y = 0, scale = 1, rotation = 0 }) => (
  <motion.div
    initial={{ x, y, scale, opacity: 0, rotate: rotation }}
    animate={{
      y: [y, y - 30, y],
      rotate: [rotation, rotation + 8, rotation - 8, rotation],
      opacity: 1
    }}
    transition={{
      duration,
      repeat: Infinity,
      ease: "easeInOut",
      delay
    }}
    className="absolute pointer-events-none select-none"
  >
    {children}
  </motion.div>
);

const LandingPage = () => {
  const navigate = useNavigate();

  useEffect(() => {
    // Force LTR for landing page to match 1:1 design exactly
    document.documentElement.dir = 'ltr';
    return () => {
      document.documentElement.dir = 'rtl';
    };
  }, []);

  return (
    <div className="relative min-h-screen w-full bg-[#0A0F14] overflow-hidden flex flex-col items-center justify-center text-white font-sans selection:bg-[#00CCFF] selection:text-[#0A0F14]">
      {/* Background Glows */}
      <div className="absolute top-[-10%] left-[-10%] w-[50%] h-[50%] bg-blue-600/10 blur-[140px] rounded-full" />
      <div className="absolute bottom-[-10%] right-[-10%] w-[50%] h-[50%] bg-cyan-600/10 blur-[140px] rounded-full" />
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-full h-full bg-[radial-gradient(circle_at_center,rgba(0,163,255,0.05)_0%,transparent_70%)] pointer-events-none" />

      {/* Floating 3D Parts - 1:1 Match with Real Assets */}
      <div className="absolute inset-0 z-0">
        {/* Piston / Engine */}
        <FloatingPart x={-450} y={-220} delay={0} duration={8} scale={1.2} rotation={15}>
          <img src="/part-family/real/engine.jpg" className="w-32 h-32 object-cover rounded-3xl opacity-20 grayscale brightness-150 mix-blend-screen shadow-2xl" alt="Piston" />
        </FloatingPart>

        {/* Brake Rotor */}
        <FloatingPart x={500} y={-180} delay={1.5} duration={10} scale={1.4} rotation={-10}>
          <img src="/part-family/real/brakes.jpg" className="w-40 h-40 object-cover rounded-full opacity-20 grayscale brightness-150 mix-blend-screen shadow-2xl" alt="Brake Rotor" />
        </FloatingPart>

        {/* Coil Spring / Suspension */}
        <FloatingPart x={-400} y={250} delay={3} duration={7} scale={1.1} rotation={45}>
          <img src="/part-family/real/suspension-steering.jpg" className="w-32 h-40 object-cover rounded-2xl opacity-20 grayscale brightness-150 mix-blend-screen shadow-2xl" alt="Coil Spring" />
        </FloatingPart>

        {/* Wheel */}
        <FloatingPart x={420} y={300} delay={0.5} duration={9} scale={1.3} rotation={-20}>
          <img src="/part-family/real/wheels-bearings.jpg" className="w-36 h-36 object-cover rounded-full opacity-20 grayscale brightness-150 mix-blend-screen shadow-2xl" alt="Wheel" />
        </FloatingPart>

        {[...Array(12)].map((_, i) => (
          <FloatingPart
            key={i}
            x={(i % 2 === 0 ? 1 : -1) * (200 + Math.random() * 600)}
            y={(i % 3 === 0 ? 1 : -1) * (100 + Math.random() * 400)}
            delay={Math.random() * 5}
            duration={10 + Math.random() * 10}
            scale={0.1 + Math.random() * 0.5}
          >
            <div className="w-2 h-2 bg-cyan-400/30 rounded-full blur-sm" />
          </FloatingPart>
        ))}
      </div>

      {/* Header - 1:1 Match: Login Left, Logo Right */}
      <header className="absolute top-0 left-0 right-0 p-6 md:p-10 flex justify-between items-center z-20">
        <Link
          to="/login"
          className="flex items-center gap-2 px-6 py-2.5 rounded-xl bg-white/5 hover:bg-white/10 border border-white/10 transition-all text-sm font-bold tracking-wide backdrop-blur-md"
        >
          <LogIn className="w-4 h-4" />
          Login
        </Link>
        <div className="flex items-center">
          <BrandLogo size="dashboard" className="!h-12 md:!h-16" blend />
        </div>
      </header>

      {/* Main Content */}
      <main className="relative z-10 text-center flex flex-col items-center max-w-5xl px-6">
        <motion.div
          initial={{ opacity: 0, y: 30 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8, ease: "easeOut" }}
          className="mb-10"
        >
          <h1 className="text-7xl md:text-9xl font-black mb-6 tracking-tighter leading-none">
            <span className="bg-gradient-to-b from-white via-white to-gray-500 bg-clip-text text-transparent">AutoSpare</span>
            <span className="bg-gradient-to-b from-[#00CCFF] to-[#0066FF] bg-clip-text text-transparent">Finder</span>
          </h1>

          <div className="space-y-4 max-w-2xl mx-auto">
            <motion.p
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: 0.4, duration: 1 }}
              className="text-xl md:text-2xl text-gray-400 font-medium tracking-tight"
            >
              The smartest way to find your car parts
            </motion.p>
            <motion.p
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: 0.6, duration: 1 }}
              className="text-2xl md:text-3xl text-white font-bold leading-relaxed"
            >
              הדרך החכמה ביותר למצוא חלקי חילוף לרכב שלך
            </motion.p>
            <motion.p
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: 0.8, duration: 1 }}
              className="text-xl md:text-2xl text-gray-500 font-medium"
            >
              أذكى طريقة للعثור على قطع غيار لسيارتك
            </motion.p>
          </div>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, scale: 0.9 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ delay: 1, duration: 0.5 }}
          className="relative group"
        >
          <div className="absolute -inset-1 bg-gradient-to-r from-[#00A3FF] to-[#0066FF] rounded-2xl blur opacity-25 group-hover:opacity-50 transition duration-1000 group-hover:duration-200" />
          <button
            onClick={() => navigate('/parts')}
            className="relative px-12 py-5 rounded-2xl bg-gradient-to-r from-[#00A3FF] to-[#0066FF] text-white font-black text-2xl shadow-2xl transition-all flex items-center gap-4 hover:translate-y-[-2px] active:translate-y-[1px]"
          >
            Start Search
            <Search className="w-7 h-7" />
          </button>
        </motion.div>
      </main>

      {/* Talk to Nir Widget Overlay */}
      <NirChatOverlay />

      {/* Footer */}
      <footer className="absolute bottom-10 left-10 text-gray-700 text-[10px] font-black uppercase tracking-[0.3em]">
        © 2026 AutoSpareFinder • Precision Engineering
      </footer>
    </div>
  );
};

export default LandingPage;
