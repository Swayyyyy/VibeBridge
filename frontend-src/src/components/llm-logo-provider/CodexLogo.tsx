import React from 'react';
import { useTheme } from '../../contexts/ThemeContext';
import { prefixUrl } from '../../utils/api';

type CodexLogoProps = {
  className?: string;
};

const CodexLogo = ({ className = 'w-5 h-5' }: CodexLogoProps) => {
  const { isDarkMode } = useTheme();

  return (
    <img
      src={prefixUrl(isDarkMode ? "/icons/codex-white.svg" : "/icons/codex.svg")}
      alt="Codex"
      className={className}
    />
  );
};

export default CodexLogo;
