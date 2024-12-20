import React, { useState, useEffect } from 'react';
import { 
  Container, 
  Card, 
  CardContent, 
  Typography, 
  Button,
  Grid,
  Switch,
  AppBar,
  Toolbar,
  Box,
  TextField,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions 
} from '@mui/material';
import axios from 'axios';

function App() {
  const [agents, setAgents] = useState({});
  const [openDialog, setOpenDialog] = useState(false);
  const [newAgent, setNewAgent] = useState({
    agent_name: '',
    full_name: '',
    role: '',
    specialty: ''
  });

  useEffect(() => {
    fetchAgents();
  }, []);

  const fetchAgents = async () => {
    const response = await axios.get('http://localhost:8501/agents');
    console.log('Current agents:', response.data);  // This will show all agents in browser console
    setAgents(response.data);
  };

  const toggleAgent = async (agentName) => {
    const newStatus = agents[agentName].status === 'active' ? 'inactive' : 'active';
    await axios.post('http://localhost:8501/toggle_agent', {
      agent_name: agentName,
      status: newStatus
    });
    fetchAgents();
  };

  const handleAddAgent = async () => {
    try {
      await axios.post('http://localhost:8501/add_agent', {
        agent_name: newAgent.agent_name,
        status: 'active',
        full_name: newAgent.full_name,
        role: newAgent.role,
        specialty: newAgent.specialty
      });
      setOpenDialog(false);
      fetchAgents();
      setNewAgent({ agent_name: '', full_name: '', role: '', specialty: '' });
    } catch (error) {
      console.error('Error adding agent:', error);
    }
  };

  return (
    <div>
      <AppBar position="static">
        <Toolbar>
          <Typography variant="h6">
            Auto Spare Parts Finder
          </Typography>
        </Toolbar>
      </AppBar>
      
      <Container maxWidth="lg" sx={{ mt: 4 }}>
        <Grid container spacing={3}>
          {Object.entries(agents).map(([name, agent]) => (
            <Grid item xs={12} md={4} key={name}>
              <Card>
                <CardContent>
                  <Typography variant="h6">{agent.full_name}</Typography>
                  <Typography color="textSecondary">{agent.role}</Typography>
                  <Typography variant="body2">
                    Specialty: {agent.specialty}
                  </Typography>
                  <Box sx={{ mt: 2, display: 'flex', alignItems: 'center' }}>
                    <Typography>Status:</Typography>
                    <Switch
                      checked={agent.status === 'active'}
                      onChange={() => toggleAgent(name)}
                      color="primary"
                    />
                    <Typography>
                      {agent.status === 'active' ? 'Active' : 'Inactive'}
                    </Typography>
                  </Box>
                </CardContent>
              </Card>
            </Grid>
          ))}
        </Grid>
      </Container>
      
      <Button
        variant="contained"
        color="primary"
        sx={{ position: 'fixed', bottom: 20, right: 20 }}
        onClick={() => setOpenDialog(true)}
      >
        Add New Agent
      </Button>

      <Dialog open={openDialog} onClose={() => setOpenDialog(false)}>
        <DialogTitle>Add New Agent</DialogTitle>
        <DialogContent>
          <TextField
            label="Agent Username"
            value={newAgent.agent_name}
            onChange={(e) => setNewAgent({...newAgent, agent_name: e.target.value})}
            fullWidth
            margin="normal"
          />
          <TextField
            label="Full Name"
            value={newAgent.full_name}
            onChange={(e) => setNewAgent({...newAgent, full_name: e.target.value})}
            fullWidth
            margin="normal"
          />
          <TextField
            label="Role"
            value={newAgent.role}
            onChange={(e) => setNewAgent({...newAgent, role: e.target.value})}
            fullWidth
            margin="normal"
          />
          <TextField
            label="Specialty"
            value={newAgent.specialty}
            onChange={(e) => setNewAgent({...newAgent, specialty: e.target.value})}
            fullWidth
            margin="normal"
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpenDialog(false)}>Cancel</Button>
          <Button onClick={handleAddAgent} variant="contained" color="primary">
            Add Agent
          </Button>
        </DialogActions>
      </Dialog>
    </div>
  );
}

export default App;