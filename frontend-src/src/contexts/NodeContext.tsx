import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { api, authenticatedFetch } from '../utils/api';

export type NodeInfo = {
  nodeId: string;
  displayName: string;
  status: 'online' | 'suspect' | 'offline';
  version: string;
  capabilities: string[];
  labels: string[];
  connectedAt: number;
  lastSeenAt: number;
};

type NodeContextType = {
  nodes: NodeInfo[];
  selectedNodeId: string | null;
  selectNode: (nodeId: string | null) => void;
  isNodeOnline: (nodeId: string) => boolean;
  isMultiNodeMode: boolean;
  refreshNodes: () => Promise<void>;
  removeNode: (nodeId: string) => Promise<void>;
};

const NodeContext = createContext<NodeContextType | null>(null);

export const useNodes = () => {
  const context = useContext(NodeContext);
  if (!context) {
    throw new Error('useNodes must be used within a NodeProvider');
  }
  return context;
};

export const useOptionalNodes = () => {
  return useContext(NodeContext);
};

const SELECTED_NODE_KEY = 'selectedNodeId';

export const NodeProvider = ({ children }: { children: React.ReactNode }) => {
  const [nodes, setNodes] = useState<NodeInfo[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(
    () => localStorage.getItem(SELECTED_NODE_KEY)
  );
  const [isMultiNodeMode, setIsMultiNodeMode] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const updateSelectedNode = useCallback((nodeId: string | null) => {
    setSelectedNodeId(nodeId);
    if (nodeId) {
      localStorage.setItem(SELECTED_NODE_KEY, nodeId);
    } else {
      localStorage.removeItem(SELECTED_NODE_KEY);
    }
  }, []);

  const fetchNodes = useCallback(async () => {
    try {
      const response = await authenticatedFetch('/api/nodes');
      if (response.ok) {
        const data = await response.json();
        const nodeList: NodeInfo[] = data.nodes || [];
        setNodes(nodeList);
        setIsMultiNodeMode(nodeList.length > 0);

        if (nodeList.length === 0) {
          if (selectedNodeId !== null) {
            updateSelectedNode(null);
          }
          return;
        }

        // Auto-select the first online node if current selection is invalid.
        const currentValid = nodeList.find(n => n.nodeId === selectedNodeId);
        if (!currentValid) {
          const firstOnline = nodeList.find(n => n.status === 'online');
          if (firstOnline) {
            updateSelectedNode(firstOnline.nodeId);
          } else if (selectedNodeId !== null) {
            updateSelectedNode(null);
          }
        }
      } else {
        // /api/nodes not available = single-node mode
        setIsMultiNodeMode(false);
        setNodes([]);
        if (selectedNodeId !== null) {
          updateSelectedNode(null);
        }
      }
    } catch {
      // Network error or not running on Main Server
      setIsMultiNodeMode(false);
      setNodes([]);
      if (selectedNodeId !== null) {
        updateSelectedNode(null);
      }
    }
  }, [selectedNodeId, updateSelectedNode]);

  const selectNode = updateSelectedNode;

  const isNodeOnline = useCallback((nodeId: string) => {
    const node = nodes.find(n => n.nodeId === nodeId);
    return node?.status === 'online';
  }, [nodes]);

  const removeNode = useCallback(async (nodeId: string) => {
    const response = await api.nodes.delete(nodeId);
    const payload = await response.json().catch(() => null);

    if (!response.ok) {
      throw new Error(
        (payload && typeof payload.detail === 'string' && payload.detail) ||
          (payload && typeof payload.error === 'string' && payload.error) ||
          'Failed to remove node',
      );
    }

    if (selectedNodeId === nodeId) {
      updateSelectedNode(null);
    }

    await fetchNodes();
  }, [fetchNodes, selectedNodeId, updateSelectedNode]);

  // Poll for node status updates
  useEffect(() => {
    fetchNodes();
    pollRef.current = setInterval(fetchNodes, 10000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [fetchNodes]);

  const value = useMemo<NodeContextType>(() => ({
    nodes,
    selectedNodeId,
    selectNode,
    isNodeOnline,
    isMultiNodeMode,
    refreshNodes: fetchNodes,
    removeNode,
  }), [nodes, selectedNodeId, selectNode, isNodeOnline, isMultiNodeMode, fetchNodes, removeNode]);

  return (
    <NodeContext.Provider value={value}>
      {children}
    </NodeContext.Provider>
  );
};

export default NodeContext;
