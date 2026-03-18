import React from 'react';
import { prefixUrl } from '../../utils/api';

type ClaudeLogoProps = {
  className?: string;
};

const ClaudeLogo = ({ className = 'w-5 h-5' }: ClaudeLogoProps) => {
  return (
    <img src={prefixUrl('/icons/claude-ai-icon.svg')} alt="Claude" className={className} />
  );
};

export default ClaudeLogo;


