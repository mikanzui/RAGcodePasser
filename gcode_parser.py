__version__ = "1.0.0"


import re
import os
import tkinter as tk
from tkinter import filedialog, ttk, scrolledtext, messagebox
from typing import List, Dict, Tuple, Optional
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import numpy as np

# ==== UPDATE CHECK ====

def check_for_updates():
    version_url = "https://raw.githubusercontent.com/YOUR_USERNAME/YOUR_REPO/main/version.txt"  # <-- Replace this
    try:
        response = requests.get(version_url, timeout=5)
        latest_version = response.text.strip()
        if latest_version != __version__:
            return latest_version
    except Exception as e:
        print("Update check failed:", e)
    return None

def run_updater():
    try:
        subprocess.Popen([sys.executable, "updater.py"])
    except Exception as e:
        messagebox.showerror("Update Failed", f"Failed to launch updater: {e}")
    sys.exit(0);


class GCodeParser:
    def __init__(self, file_path: str = None, content: str = None):
        """
        Initialize the G-code parser with either a file path or content string
        
        Args:
            file_path: Path to the G-code file
            content: G-code content as a string
        """
        self.file_path = file_path
        self.content = content
        self.lines = []
        self.tool_changes = []
        self.retractions = []
        
        if file_path:
            self._load_from_file()
        elif content:
            self._load_from_content()
    
    def _load_from_file(self):
        """Load G-code from a file"""
        try:
            with open(self.file_path, 'r') as file:
                self.lines = file.readlines()
        except Exception as e:
            print(f"Error reading file: {e}")
            self.lines = []
    
    def _load_from_content(self):
        """Load G-code from a string"""
        self.lines = self.content.splitlines()
    
    def parse(self):
        """Parse the loaded G-code to detect tool changes and retractions"""
        self.detect_tool_changes()
        self.detect_retractions()
    
    def detect_tool_changes(self):
        """Detect all tool change operations in the G-code"""
        tool_change_patterns = [
            r'T(\d+)',               # Tool change by T command
            r'M6\s+T(\d+)',          # Tool change with M6
            r'M06\s+T(\d+)',         # Alternative tool change syntax
            r'M61\s+Q(\d+)',         # Set current tool number
        ]
        
        self.tool_changes = []
        
        for line_number, line in enumerate(self.lines, 1):
            line = line.strip()
            
            # Skip comments and empty lines
            if line.startswith(';') or line.startswith('(') or not line:
                continue
            
            # Remove inline comments for processing
            clean_line = re.sub(r'\([^)]*\)', '', line)  # Remove parenthesis comments
            clean_line = re.sub(r';.*', '', clean_line)  # Remove semicolon comments
            
            for pattern in tool_change_patterns:
                match = re.search(pattern, clean_line)
                if match:
                    tool_number = match.group(1)
                    self.tool_changes.append({
                        'line_number': line_number,
                        'tool_number': tool_number,
                        'line_content': line
                    })
                    break
    
    def detect_retractions(self):
        """
        Detect retraction heights in the G-code using smart detection.
        
        A retraction is identified when:
        1. Z-axis moves above a threshold height (detects first significant Z-rise)
        2. Followed by rapid movements (G0/G00)
        3. Groups similar retraction heights to identify consistent patterns
        """
        self.retractions = []
        
        # Track position
        current_pos = {'X': 0.0, 'Y': 0.0, 'Z': 0.0}
        prev_z = 0.0
        in_retraction = False
        retraction_start_line = 0
        retraction_start_height = 0.0
        
        # Movement patterns
        z_movement_pattern = r'[GgMm](?:\d+\.?\d*)\s+.*[Zz]([-+]?\d*\.?\d+)'  # Z movement in any command
        rapid_move_pattern = r'[Gg]0'  # G0 indicates rapid movement
        position_pattern = r'[XxYyZz]([-+]?\d*\.?\d+)'  # Coordinate values
        
        # Analyze line by line
        for line_number, line in enumerate(self.lines, 1):
            line = line.strip()
            
            # Skip comments and empty lines
            if line.startswith(';') or line.startswith('(') or not line:
                continue
            
            # Remove inline comments
            clean_line = re.sub(r'\([^)]*\)', '', line) 
            clean_line = re.sub(r';.*', '', clean_line)
            
            # Extract all position coordinates from the line
            position_matches = re.finditer(position_pattern, clean_line)
            for match in position_matches:
                axis = match.group(0)[0].upper()
                value = float(match.group(1))
                current_pos[axis] = value
            
            # Check if this is a Z movement
            is_z_movement = re.search(z_movement_pattern, clean_line) is not None
            is_rapid = re.search(rapid_move_pattern, clean_line) is not None
            
            # Detect potential retraction start (significant Z increase)
            if is_z_movement and current_pos['Z'] > prev_z + 0.5:  # Threshold for retraction (0.5mm rise)
                if not in_retraction:
                    in_retraction = True
                    retraction_start_line = line_number
                    retraction_start_height = current_pos['Z']
            
            # If we're in a retraction state and there's a rapid move, record it
            if in_retraction and is_rapid:
                self.retractions.append({
                    'line_number': retraction_start_line,
                    'z_height': retraction_start_height,
                    'line_content': self.lines[retraction_start_line-1].strip(),
                    'end_line': line_number,
                    'end_content': line
                })
                
                # Reset retraction state if we're moving down again
                if current_pos['Z'] < retraction_start_height:
                    in_retraction = False
            
            # Update previous Z position
            prev_z = current_pos['Z']
        
        # Post-process to combine similar retraction heights (within 0.1mm tolerance)
        self._group_similar_retractions()
    
    def _group_similar_retractions(self):
        """Group similar retraction heights to identify common retraction heights"""
        if not self.retractions:
            return
            
        # Sort by height
        self.retractions.sort(key=lambda x: x['z_height'])
        
        # Group similar heights
        grouped_retractions = []
        current_group = [self.retractions[0]]
        
        for i in range(1, len(self.retractions)):
            current = self.retractions[i]
            prev = current_group[-1]
            
            # If heights are similar (within 0.1mm), add to current group
            if abs(current['z_height'] - prev['z_height']) < 0.1:
                current_group.append(current)
            else:
                # Calculate average height for the group
                avg_height = sum(r['z_height'] for r in current_group) / len(current_group)
                
                # Update all entries in the group with the average height
                for r in current_group:
                    r['z_height'] = round(avg_height, 3)
                    r['grouped'] = True
                    r['group_size'] = len(current_group)
                
                # Add processed group to result
                grouped_retractions.extend(current_group)
                
                # Start new group
                current_group = [current]
        
        # Process the last group
        if current_group:
            avg_height = sum(r['z_height'] for r in current_group) / len(current_group)
            for r in current_group:
                r['z_height'] = round(avg_height, 3)
                r['grouped'] = True
                r['group_size'] = len(current_group)
            grouped_retractions.extend(current_group)
        
        self.retractions = grouped_retractions
    
    def get_tool_changes(self) -> List[Dict]:
        """Get the detected tool changes"""
        return self.tool_changes
    
    def get_retractions(self) -> List[Dict]:
        """Get the detected retractions"""
        return self.retractions
    
    def summarize(self) -> str:
        """Generate a summary of the detected features"""
        summary = []
        
        summary.append("=== G-code Analysis Summary ===")
        
        # Tool changes summary
        summary.append("\nTool Changes:")
        if self.tool_changes:
            for tc in self.tool_changes:
                summary.append(f"Line {tc['line_number']}: Tool T{tc['tool_number']} - {tc['line_content']}")
        else:
            summary.append("No tool changes detected.")
        
        # Retractions summary
        summary.append("\nRetraction Heights:")
        if self.retractions:
            # Group retractions by height
            height_groups = {}
            for r in self.retractions:
                height = r['z_height']
                if height not in height_groups:
                    height_groups[height] = []
                height_groups[height].append(r)
            
            # Show retraction heights and occurrences
            for height, retractions in sorted(height_groups.items()):
                first = retractions[0]
                summary.append(f"Z Height: {height:.3f} (first on line {first['line_number']})")
                
                # Show sample of the retraction movements
                if len(retractions) > 1:
                    summary.append(f"  Found in {len(retractions)} movements")
                    
                    # Get range of motion at this height (analyze X/Y coordinates if needed)
                    # This could be enhanced to show more metrics in the future
            
            # Identify likely primary retraction height
            if height_groups:
                largest_group = max(height_groups.items(), key=lambda x: len(x[1]))
                summary.append(f"\nPrimary Retraction Height: {largest_group[0]:.3f}")
                summary.append(f"  Used in {len(largest_group[1])} out of {len(self.retractions)} retractions")
                summary.append(f"  ({(len(largest_group[1])/len(self.retractions)*100):.1f}% of all retractions)")
        else:
            summary.append("No retractions detected.")
        
        return "\n".join(summary)


class GCodeParserApp:
    def __init__(self, root):
        """Initialize the Tkinter GUI application
        
        Args:
            root: The Tkinter root window
        """
        self.root = root
        self.root.title("G-Code Parser")
        self.root.geometry("900x700")
        self.root.minsize(800, 600)
        
        self.gcode_parser = None
        self.setup_ui()
    
    def setup_ui(self):
        """Set up the user interface elements"""
        # Create a main frame with padding
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # File selection frame
        file_frame = ttk.LabelFrame(main_frame, text="G-Code File", padding="5")
        file_frame.pack(fill=tk.X, pady=5)
        
        self.file_path_var = tk.StringVar()
        ttk.Label(file_frame, text="File:").grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)
        ttk.Entry(file_frame, textvariable=self.file_path_var, width=60).grid(row=0, column=1, padx=5, pady=5, sticky=tk.EW)
        ttk.Button(file_frame, text="Browse...", command=self.browse_file).grid(row=0, column=2, padx=5, pady=5)
        ttk.Button(file_frame, text="Parse G-Code", command=self.parse_gcode).grid(row=0, column=3, padx=5, pady=5)
        
        file_frame.columnconfigure(1, weight=1)
        
        # Create notebook for different views
        self.notebook = ttk.Notebook(main_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # G-code content tab
        self.gcode_frame = ttk.Frame(self.notebook, padding="5")
        self.notebook.add(self.gcode_frame, text="G-Code Content")
        
        # Create G-code display
        self.gcode_text = scrolledtext.ScrolledText(self.gcode_frame, wrap=tk.NONE, height=20, font=("Courier", 10))
        self.gcode_text.pack(fill=tk.BOTH, expand=True)
        
        # Tool changes tab
        self.tools_frame = ttk.Frame(self.notebook, padding="5")
        self.notebook.add(self.tools_frame, text="Tool Changes")
        
        # Create tool changes treeview
        self.tools_tree = ttk.Treeview(self.tools_frame, columns=("Line", "Tool", "Content"), show="headings")
        self.tools_tree.heading("Line", text="Line Number")
        self.tools_tree.heading("Tool", text="Tool Number")
        self.tools_tree.heading("Content", text="Line Content")
        self.tools_tree.column("Line", width=100, anchor=tk.CENTER)
        self.tools_tree.column("Tool", width=100, anchor=tk.CENTER)
        self.tools_tree.column("Content", width=400)
        
        tools_scroll_y = ttk.Scrollbar(self.tools_frame, orient=tk.VERTICAL, command=self.tools_tree.yview)
        self.tools_tree.configure(yscrollcommand=tools_scroll_y.set)
        
        self.tools_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tools_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Retractions tab
        self.retractions_frame = ttk.Frame(self.notebook, padding="5")
        self.notebook.add(self.retractions_frame, text="Retractions")
        
        # Create retractions treeview
        self.retractions_tree = ttk.Treeview(self.retractions_frame, 
                                           columns=("Line", "Height", "Content", "End"), 
                                           show="headings")
        self.retractions_tree.heading("Line", text="Line Number")
        self.retractions_tree.heading("Height", text="Z Height")
        self.retractions_tree.heading("Content", text="Line Content")
        self.retractions_tree.heading("End", text="End Line")
        self.retractions_tree.column("Line", width=80, anchor=tk.CENTER)
        self.retractions_tree.column("Height", width=100, anchor=tk.CENTER)
        self.retractions_tree.column("Content", width=350)
        self.retractions_tree.column("End", width=80, anchor=tk.CENTER)
        
        retractions_scroll_y = ttk.Scrollbar(self.retractions_frame, orient=tk.VERTICAL, 
                                           command=self.retractions_tree.yview)
        self.retractions_tree.configure(yscrollcommand=retractions_scroll_y.set)
        
        self.retractions_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        retractions_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Summary tab
        self.summary_frame = ttk.Frame(self.notebook, padding="5")
        self.notebook.add(self.summary_frame, text="Summary")
        
        # Create summary text
        self.summary_text = scrolledtext.ScrolledText(self.summary_frame, wrap=tk.WORD, height=20, font=("Arial", 10))
        self.summary_text.pack(fill=tk.BOTH, expand=True)
        
        # Visualization tab
        self.visual_frame = ttk.Frame(self.notebook, padding="5")
        self.notebook.add(self.visual_frame, text="Visualization")

        # Controls for tool selection and view
        controls_frame = ttk.Frame(self.visual_frame)
        controls_frame.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(controls_frame, text="Select Tool(s):").pack(side=tk.LEFT, padx=5)
        self.tool_select_var = tk.Variable(value=[])
        self.tool_listbox = tk.Listbox(controls_frame, listvariable=self.tool_select_var, selectmode=tk.MULTIPLE, height=3, exportselection=False)
        self.tool_listbox.pack(side=tk.LEFT, padx=5)
        ttk.Label(controls_frame, text="View:").pack(side=tk.LEFT, padx=5)
        self.view_var = tk.StringVar(value="3D")
        for view in ["3D", "XY", "XZ", "YZ"]:
            ttk.Radiobutton(controls_frame, text=view, variable=self.view_var, value=view, command=self.update_visualization).pack(side=tk.LEFT)
        ttk.Button(controls_frame, text="Update", command=self.update_visualization).pack(side=tk.LEFT, padx=5)

        # Add Reset View and Zoom controls
        ttk.Button(controls_frame, text="Reset View", command=self.reset_view).pack(side=tk.LEFT, padx=5)
        ttk.Label(controls_frame, text="Zoom:").pack(side=tk.LEFT, padx=2)
        self.zoom_var = tk.DoubleVar(value=1.0)
        self.zoom_scale = ttk.Scale(controls_frame, from_=0.2, to=3.0, orient=tk.HORIZONTAL, variable=self.zoom_var, command=lambda e: self.update_visualization())
        self.zoom_scale.pack(side=tk.LEFT, padx=2)

        # Matplotlib Figure
        self.fig = Figure(figsize=(6, 5))
        self.ax = self.fig.add_subplot(111, projection='3d')
        self._initial_elev = self.ax.elev
        self._initial_azim = self.ax.azim
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.visual_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        # Status bar
        status_frame = ttk.Frame(main_frame)
        status_frame.pack(fill=tk.X, pady=5)
        
        self.status_var = tk.StringVar()
        self.status_var.set("Ready. Please select a G-code file.")
        ttk.Label(status_frame, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W).pack(fill=tk.X)
    
    def browse_file(self):
        """Open file dialog to select a G-code file"""
        file_path = filedialog.askopenfilename(
            title="Select G-Code File",
            filetypes=(
                ("G-Code files", "*.gcd *.gcode *.nc *.ngc *.tap"),
                ("Text files", "*.txt"),
                ("All files", "*.*")
            )
        )
        
        if file_path:
            self.file_path_var.set(file_path)
            self.load_gcode_content(file_path)
    
    def load_gcode_content(self, file_path):
        """Load and display G-code content"""
        try:
            with open(file_path, 'r') as f:
                content = f.read()
            
            self.gcode_text.delete(1.0, tk.END)
            self.gcode_text.insert(tk.END, content)
            
            # Add line numbers for reference
            self.update_gcode_display()
            
            self.status_var.set(f"Loaded file: {os.path.basename(file_path)}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load file: {str(e)}")
            self.status_var.set("Error loading file.")
    
    def update_gcode_display(self):
        """Update the G-code display with line numbers"""
        content = self.gcode_text.get(1.0, tk.END)
        lines = content.splitlines()
        
        self.gcode_text.delete(1.0, tk.END)
        
        for i, line in enumerate(lines, 1):
            self.gcode_text.insert(tk.END, f"{i:4d} | {line}\n")
    
    def parse_gcode(self):
        """Parse the loaded G-code file and update displays"""
        file_path = self.file_path_var.get()
        
        if not file_path:
            messagebox.showinfo("Information", "Please select a G-code file first.")
            return
        
        try:
            # Create parser and parse the file
            self.gcode_parser = GCodeParser(file_path=file_path)
            self.gcode_parser.parse()
            
            # Update tool changes display
            self.update_tool_changes_display()
            
            # Update retractions display
            self.update_retractions_display()
            
            # Update summary display
            self.update_summary_display()
            
            # Update tool listbox for visualization
            self.update_tool_listbox()
            
            # Update visualization
            self.update_visualization()
            
            # Switch to the summary tab
            self.notebook.select(self.summary_frame)
            
            self.status_var.set(f"Parsed file: {os.path.basename(file_path)}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to parse file: {str(e)}")
            self.status_var.set("Error parsing file.")
    
    def update_tool_changes_display(self):
        """Update the tool changes treeview with parsed data"""
        # Clear existing data
        for item in self.tools_tree.get_children():
            self.tools_tree.delete(item)
        
        # Add tool changes
        for tc in self.gcode_parser.get_tool_changes():
            values = (tc['line_number'], tc['tool_number'], tc['line_content'])
            self.tools_tree.insert('', tk.END, values=values)
    
    def update_retractions_display(self):
        """Update the retractions treeview with parsed data"""
        # Clear existing data
        for item in self.retractions_tree.get_children():
            self.retractions_tree.delete(item)
        
        # Add retractions
        for r in self.gcode_parser.get_retractions():
            group_info = f" (Group of {r.get('group_size', 1)})" if r.get('grouped', False) else ""
            values = (
                r['line_number'], 
                f"{r['z_height']:.3f}{group_info}", 
                r['line_content'],
                r.get('end_line', '')
            )
            self.retractions_tree.insert('', tk.END, values=values)
            
        # If retractions were found, add a summary at the top
        if self.gcode_parser.get_retractions():
            # Find most common retraction height
            heights = {}
            for r in self.gcode_parser.get_retractions():
                h = r['z_height']
                if h not in heights:
                    heights[h] = 0
                heights[h] += 1
            
            if heights:
                most_common = max(heights.items(), key=lambda x: x[1])
                self.retractions_tree.insert('', 0, values=(
                    "SUMMARY", 
                    f"Most common: {most_common[0]:.3f}", 
                    f"Found {most_common[1]} occurrences",
                    "---"
                ), tags=('summary',))
                self.retractions_tree.tag_configure('summary', background='#e0e0ff')
    
    def update_summary_display(self):
        """Update the summary text with parsed data"""
        summary = self.gcode_parser.summarize()
        
        self.summary_text.delete(1.0, tk.END)
        self.summary_text.insert(tk.END, summary)
    
    def update_tool_listbox(self):
        """Update the tool selection listbox with available tools"""
        if not self.gcode_parser:
            return
        tools = sorted(set(tc['tool_number'] for tc in self.gcode_parser.get_tool_changes()))
        self.tool_listbox.delete(0, tk.END)
        for t in tools:
            self.tool_listbox.insert(tk.END, t)
        # Select all by default
        self.tool_listbox.select_set(0, tk.END)

    def extract_tool_paths(self):
        """Extract tool paths split by tool number. Returns dict: tool_number -> list of [X,Y,Z]"""
        if not self.gcode_parser:
            return {}
        lines = self.gcode_parser.lines
        tool_changes = self.gcode_parser.get_tool_changes()
        tool_paths = {}
        current_tool = None
        current_path = []
        current_pos = {'X': 0.0, 'Y': 0.0, 'Z': 0.0}
        tc_idx = 0
        for i, line in enumerate(lines):
            line = line.strip()
            if not line or line.startswith(';') or line.startswith('('):
                continue
            # Tool change?
            if tc_idx < len(tool_changes) and (i+1) == tool_changes[tc_idx]['line_number']:
                # Save previous tool path
                if current_tool is not None and current_path:
                    tool_paths.setdefault(current_tool, []).extend(current_path)
                current_tool = tool_changes[tc_idx]['tool_number']
                current_path = []
                tc_idx += 1
            # Extract X/Y/Z
            for axis in 'XYZ':
                m = re.search(rf'{axis}([-+]?\d*\.?\d+)', line, re.IGNORECASE)
                if m:
                    current_pos[axis] = float(m.group(1))
            current_path.append([current_pos['X'], current_pos['Y'], current_pos['Z']])
        # Save last tool path
        if current_tool is not None and current_path:
            tool_paths.setdefault(current_tool, []).extend(current_path)
        return tool_paths

    def extract_rapid_segments(self):
        """Extract all rapid (G0/G00) move segments for each tool. Returns dict: tool_number -> list of (start, end) points."""
        if not self.gcode_parser:
            return {}
        lines = self.gcode_parser.lines
        tool_changes = self.gcode_parser.get_tool_changes()
        rapid_segments = {}
        current_tool = None
        tc_idx = 0
        current_pos = {'X': 0.0, 'Y': 0.0, 'Z': 0.0}
        prev_pos = None
        for i, line in enumerate(lines):
            line = line.strip()
            if not line or line.startswith(';') or line.startswith('('):
                continue
            # Tool change?
            if tc_idx < len(tool_changes) and (i+1) == tool_changes[tc_idx]['line_number']:
                current_tool = tool_changes[tc_idx]['tool_number']
                tc_idx += 1
            # Extract X/Y/Z
            for axis in 'XYZ':
                m = re.search(rf'{axis}([-+]?\d*\.?\d+)', line, re.IGNORECASE)
                if m:
                    current_pos[axis] = float(m.group(1))
            # Detect rapid move (G0/G00)
            if re.match(r'G0\b|G00\b', line, re.IGNORECASE):
                if prev_pos is not None and current_tool is not None:
                    seg = ([prev_pos['X'], prev_pos['Y'], prev_pos['Z']],
                           [current_pos['X'], current_pos['Y'], current_pos['Z']])
                    rapid_segments.setdefault(current_tool, []).append(seg)
            prev_pos = current_pos.copy()
        return rapid_segments

    def _on_figure_enter(self, event):
        self._mouse_over_canvas = True

    def _on_figure_leave(self, event):
        self._mouse_over_canvas = False

    def _on_scroll_zoom(self, event):
        if self._mouse_over_canvas:
            # Zoom in/out with scroll wheel
            zoom = self.zoom_var.get()
            if event.button == 'up':
                zoom = min(zoom * 1.1, 3.0)
            elif event.button == 'down':
                zoom = max(zoom / 1.1, 0.2)
            self.zoom_var.set(zoom)
            self.update_visualization()

    def reset_view(self):
        """Reset the view to default zoom, axis limits, and camera orientation"""
        self.zoom_var.set(1.0)
        self.ax.view_init(elev=self._initial_elev, azim=self._initial_azim)
        self.update_visualization()

    def update_visualization(self):
        """Update the 3D/2D visualization of tool paths"""
        if not self.gcode_parser:
            return
        # Parse tool paths by tool
        tool_paths = self.extract_tool_paths()
        rapid_segments = self.extract_rapid_segments()
        selected_indices = self.tool_listbox.curselection()
        selected_tools = [self.tool_listbox.get(i) for i in selected_indices] if selected_indices else list(tool_paths.keys())
        view = self.view_var.get()
        self.ax.clear()
        colors = ['b', 'g', 'r', 'c', 'm', 'y', 'k']
        for idx, tool in enumerate(selected_tools):
            path = tool_paths.get(tool, [])
            if not path:
                continue
            arr = np.array(path)
            if arr.shape[0] < 2:
                continue
            color = colors[idx % len(colors)]
            # Plot main tool path (solid, only non-rapid moves)
            # For simplicity, plot all as solid, then overlay rapids as dotted
            if view == "3D":
                self.ax.plot(arr[:,0], arr[:,1], arr[:,2], label=f"Tool {tool}", color=color)
                self.ax.set_xlabel('X')
                self.ax.set_ylabel('Y')
                self.ax.set_zlabel('Z')
            elif view == "XY":
                self.ax.plot(arr[:,0], arr[:,1], label=f"Tool {tool}", color=color)
                self.ax.set_xlabel('X')
                self.ax.set_ylabel('Y')
            elif view == "XZ":
                self.ax.plot(arr[:,0], arr[:,2], label=f"Tool {tool}", color=color)
                self.ax.set_xlabel('X')
                self.ax.set_ylabel('Z')
            elif view == "YZ":
                self.ax.plot(arr[:,1], arr[:,2], label=f"Tool {tool}", color=color)
                self.ax.set_xlabel('Y')
                self.ax.set_ylabel('Z')
            # Plot rapid (G0/G00) segments as more visible dashed lines
            rapids = rapid_segments.get(tool, [])
            for seg in rapids:
                p1, p2 = np.array(seg[0]), np.array(seg[1])
                dash_style = (0, (8, 8))  # 8pt on, 8pt off for a clear dashed look
                if view == "3D":
                    self.ax.plot([p1[0], p2[0]], [p1[1], p2[1]], [p1[2], p2[2]], color=color, linestyle=(0, (8, 8)), linewidth=2, alpha=0.8)
                elif view == "XY":
                    self.ax.plot([p1[0], p2[0]], [p1[1], p2[1]], color=color, linestyle=(0, (8, 8)), linewidth=2, alpha=0.8)
                elif view == "XZ":
                    self.ax.plot([p1[0], p2[0]], [p1[2], p2[2]], color=color, linestyle=(0, (8, 8)), linewidth=2, alpha=0.8)
                elif view == "YZ":
                    self.ax.plot([p1[1], p2[1]], [p1[2], p2[2]], color=color, linestyle=(0, (8, 8)), linewidth=2, alpha=0.8)
        self.ax.legend()
        self.ax.set_title(f"Tool Paths ({view} view)")

        # Ensure equal aspect ratio for all axes (so circles look circular), and apply zoom
        if view == "3D":
            all_points = np.concatenate([np.array(tool_paths[t]) for t in selected_tools if t in tool_paths and len(tool_paths[t]) > 0])
            if all_points.shape[0] > 0:
                x_min, y_min, z_min = np.min(all_points, axis=0)
                x_max, y_max, z_max = np.max(all_points, axis=0)
                max_range = max(x_max - x_min, y_max - y_min, z_max - z_min)
                zoom = self.zoom_var.get()
                # Center each axis
                x_mid = (x_max + x_min) / 2
                y_mid = (y_max + y_min) / 2
                z_mid = (z_max + z_min) / 2
                half = (max_range / 2) / zoom
                self.ax.set_xlim(x_mid - half, x_mid + half)
                self.ax.set_ylim(y_mid - half, y_mid + half)
                self.ax.set_zlim(z_mid - half, z_mid + half)
        self.canvas.draw()

    def _tool_for_line(self, line_number):
        """Helper: Return the tool number active at a given line number"""
        tool_changes = self.gcode_parser.get_tool_changes()
        current_tool = None
        for tc in tool_changes:
            if line_number >= tc['line_number']:
                current_tool = tc['tool_number']
            else:
                break
        return current_tool


def main():
    """Launch the Tkinter application"""
    root = tk.Tk()
    app = GCodeParserApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

    # ==== MINIMAL GUI for testing ====

class GCodeParserApp:
    def __init__(self, root):
        self.root = root
        self.root.title(f"G-Code Parser v{__version__}")
        self.setup_ui()

    def setup_ui(self):
        frame = ttk.Frame(self.root, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text="Welcome to G-Code Parser").pack(pady=10)
        ttk.Button(frame, text="Check for Updates", command=self.check_and_update).pack(pady=5)
        ttk.Button(frame, text="Exit", command=self.root.quit).pack(pady=5)

    def check_and_update(self):
        latest = check_for_updates()
        if latest:
            if messagebox.askyesno("Update Available", f"A new version ({latest}) is available. Update now?"):
                run_updater()
        else:
            messagebox.showinfo("Up to Date", "You're running the latest version.")

def main():
    root = tk.Tk()
    app = GCodeParserApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()