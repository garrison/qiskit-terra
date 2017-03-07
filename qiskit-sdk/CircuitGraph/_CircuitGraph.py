"""
Object to represent a quantum circuit as a directed acyclic graph.

The nodes in the graph are either input/output nodes or operation nodes.
The operation nodes are elements of a basis that is part of the circuit.
The QASM definitions of the basis elements are carried with the circuit.
The edges correspond to qubits or bits in the circuit. A directed edge
from node A to node B means that the (qu)bit passes from the output of A
to the input of B. The object's methods allow circuits to be constructed,
composed, and modified. Some natural properties like depth can be computed
directly from the graph.

Author: Andrew Cross
"""
import networkx as nx
import itertools
import copy
from ._CircuitGraphError import CircuitGraphError


class CircuitGraph:
    """
    Quantum circuit as a directed acyclic graph.

    There are 3 types of nodes in the graph: inputs, outputs, and operations.
    The nodes are connected by directed edges that correspond to qubits and
    bits.
    """

    def __init__(self):
        """Create an empty circuit."""
        # Map from a wire's name (reg,idx) to a Bool that is True if the
        # wire is a classical bit and False if the wire is a qubit.
        self.wire_type = {}

        # Map from wire names (reg,idx) to input nodes of the graph
        self.input_map = {}

        # Map from wire names (reg,idx) to output nodes of the graph
        self.output_map = {}

        # Running count of the total number of nodes
        self.node_counter = 0

        # Map of named operations in this circuit and their signatures.
        # The signature is an integer tuple (nq,nc,np) specifying the
        # number of input qubits, input bits, and real parameters.
        # The definition is external to the circuit object.
        self.basis = {}

        # Directed multigraph whose nodes are inputs, outputs, or operations.
        # Operation nodes have equal in- and out-degrees and carry
        # additional data about the operation, including the argument order
        # and parameter values.
        # Input nodes have out-degree 1 and output nodes have in-degree 1.
        # Edges carry wire labels (reg,idx) and each operation has
        # corresponding in- and out-edges with the same wire labels.
        self.G = nx.MultiDiGraph()

        # Map of qregs to sizes
        self.qregs = {}

        # Map of cregs to sizes
        self.cregs = {}

        # Map of user defined gates to ast nodes defining them
        self.gates = {}

        # Output precision for printing floats
        self.prec = 10

    def rename_register(self, regname, newname):
        """Rename a classical or quantum register throughout the circuit.

        regname = existing register name string
        newname = replacement register name string
        """
        if regname == newname:
            return
        if newname in self.qregs or newname in self.cregs:
            raise CircuitGraphError("duplicate register name %s" % newname)
        if regname not in self.qregs and regname not in self.cregs:
            raise CircuitGraphError("no register named %s" % regname)
        iscreg = False
        if regname in self.qregs:
            self.qregs[newname] = self.qregs[regname]
            self.qregs.pop(regname, None)
            regsz = self.qregs[newname]
        if regname in self.cregs:
            self.cregs[newname] = self.cregs[regname]
            self.cregs.pop(regname, None)
            regsz = self.cregs[newname]
            iscreg = True
        for i in range(regsz):
            self.wire_type[(newname, i)] = iscreg
            self.wire_type.pop((regname, i), None)
            self.input_map[(newname, i)] = self.input_map[(regname, i)]
            self.input_map.pop((regname, i), None)
            self.output_map[(newname, i)] = self.output_map[(regname, i)]
            self.output_map.pop((regname, i), None)
        for n, d in self.G.nodes_iter(data=True):
            if d["type"] == "in" or d["type"] == "out":
                if d["name"][0] == regname:
                    d["name"] = (newname, d["name"][1])
            elif d["type"] == "op":
                qa = []
                for a in d["qargs"]:
                    if a[0] == regname:
                        a = (newname, a[1])
                    qa.append(a)
                d["qargs"] = qa
                ca = []
                for a in d["cargs"]:
                    if a[0] == regname:
                        a = (newname, a[1])
                    ca.append(a)
                d["cargs"] = ca
                if d["condition"] is not None:
                    if d["condition"][0] == regname:
                        d["condition"] = (newname, d["condition"][1])
        for e1, e2, d in self.G.edges_iter(data=True):
            if d["name"][0] == regname:
                d["name"] = (newname, d["name"][1])

    def remove_all_ops_named(self, opname):
        """Remove all operation nodes with the given name."""
        nlist = self.get_named_nodes(opname)
        for n in nlist:
            self._remove_op_node(n)

    def deepcopy(self):
        """Return a deep copy of self."""
        return copy.deepcopy(self)

    def fs(self, f):
        """Format a float f as a string with self.prec digits."""
        fmt = "{0:0.%sf}" % self.prec
        return fmt.format(f)

    def add_qreg(self, name, sz):
        """Add all wires in a quantum register named name with size sz."""
        if name in self.qregs or name in self.cregs:
            raise CircuitGraphError("duplicate register name %s" % name)
        self.qregs[name] = sz
        for j in range(sz):
            self._add_wire((name, j))

    def add_creg(self, name, sz):
        """Add all wires in a classical register named name with size sz."""
        if name in self.qregs or name in self.cregs:
            raise CircuitGraphError("duplicate register name %s" % name)
        self.cregs[name] = sz
        for j in range(sz):
            self._add_wire((name, j), True)

    def _add_wire(self, name, isClassical=False):
        """Add a qubit or bit to the circuit.

        name is a (string,int) tuple containing register name and index
        This adds a pair of in and out nodes connected by an edge.
        """
        if name not in self.wire_type:
            self.wire_type[name] = isClassical
            self.node_counter += 1
            self.input_map[name] = self.node_counter
            self.node_counter += 1
            self.output_map[name] = self.node_counter
            in_node = self.input_map[name]
            out_node = self.output_map[name]
            self.G.add_edge(in_node, out_node)
            self.G.node[in_node]["type"] = "in"
            self.G.node[out_node]["type"] = "out"
            self.G.node[in_node]["name"] = name
            self.G.node[out_node]["name"] = name
            self.G.edge[in_node][out_node][0]["name"] = name
        else:
            raise CircuitGraphError("duplicate wire %s" % name)

    def add_basis_element(self, name, nq, nc=0, np=0):
        """Add an operation to the basis.

        name is string label for operation
        nq is number of qubit arguments
        nc is number of bit arguments
        np is number of real parameters

        The parameters (nq,nc,np) are ignored for the special case
        when name = "barrier". The barrier instruction has a variable
        number of qubit arguments.
        """
        if name not in self.basis:
            self.basis[name] = (nq, nc, np)
        if name in self.gates:
            if self.gates[name]["n_args"] != np or \
              self.gates[name]["n_bits"] != nq or nc != 0:
                raise CircuitGraphError("gate data does not match "
                                        + "basis element specification")

    def add_gate_data(self, name, gatedata):
        """Add the definition of a gate.

        gatedata is dict with fields:
        "opaque" = True or False
        "n_args" = number of real parameters
        "n_bits" = number of qubits
        "args"   = list of parameter names
        "bits"   = list of qubit names
        "body"   = GateBody AST node
        """
        if name not in self.gates:
            self.gates[name] = gatedata
            if name in self.basis:
                if self.basis[name][0] != self.gates[name]["n_bits"] or \
                  self.basis[name][1] != 0 or \
                  self.basis[name][2] != self.gates[name]["n_args"]:
                    raise CircuitGraphError("gate data does not match "
                                            + "basis element specification")

    def _check_basis_data(self, name, qargs, cargs, params):
        """Check the arguments against the data for this operation.

        name is a string
        qargs is a list of tuples like ("q",0)
        cargs is a list of tuples like ("c",0)
        params is a list of strings that represent floats
        """
        # Check that we have this operation
        if name not in self.basis:
            raise CircuitGraphError("%s is not in the list of basis operations"
                                    % name)

        # Check the number of arguments matches the signature
        if name != "barrier":
            if len(qargs) != self.basis[name][0]:
                raise CircuitGraphError("incorrect number of qubits for %s"
                                        % name)
            if len(cargs) != self.basis[name][1]:
                raise CircuitGraphError("incorrect number of bits for %s"
                                        % name)
            if len(params) != self.basis[name][2]:
                raise CircuitGraphError("incorrect number of parameters for %s"
                                        % name)
        else:
            # "barrier" is a special case
            if len(qargs) == 0:
                raise CircuitGraphError("incorrect number of qubits for %s"
                                        % name)
            if len(cargs) != 0:
                raise CircuitGraphError("incorrect number of bits for %s"
                                        % name)
            if len(params) != 0:
                raise CircuitGraphError("incorrect number of parameters for %s"
                                        % name)

    def _check_condition(self, name, condition):
        """Verify that the condition is valid.

        name is a string used for error reporting
        condition is either None or a tuple (string,int) giving (creg,value)
        """
        # Verify creg exists
        if condition is not None and condition[0] not in self.cregs:
            raise CircuitGraphError("invalid creg in condition for %s" % name)

    def _check_bits(self, args, amap, bval):
        """Check the values of a list of (qu)bit arguments.

        For each element A of args, check that amap contains A and
        self.wire_type[A] equals bval.
        args is a list of (regname,idx) tuples
        amap is a dictionary keyed on (regname,idx) tuples
        bval is boolean
        """
        # Check for each wire
        for q in args:
            if q not in amap:
                raise CircuitGraphError("(qu)bit %s not found" % q)
            if self.wire_type[q] != bval:
                raise CircuitGraphError("expected wire type %s for %s"
                                        % (bval, q))

    def _bits_in_condition(self, cond):
        """Return a list of bits (regname,idx) in the given condition.

        cond is either None or a (regname,int) tuple specifying
        a classical if condition.
        """
        all_bits = []
        if cond is not None:
            all_bits.extend([(cond[0], j) for j in range(self.cregs[cond[0]])])
        return all_bits

    def _add_op_node(self, nname, nqargs, ncargs, nparams, ncondition):
        """Add a new operation node to the graph and assign properties.

        nname node name
        nqargs quantum arguments
        ncargs classical arguments
        nparams parameters
        ncondition classical condition (or None)
        """
        # Add a new operation node to the graph
        self.node_counter += 1
        self.G.add_node(self.node_counter)
        # Update that operation node's data
        self.G.node[self.node_counter]["type"] = "op"
        self.G.node[self.node_counter]["name"] = nname
        self.G.node[self.node_counter]["qargs"] = nqargs
        self.G.node[self.node_counter]["cargs"] = ncargs
        self.G.node[self.node_counter]["params"] = nparams
        self.G.node[self.node_counter]["condition"] = ncondition

    def apply_operation_back(self, name, qargs, cargs=[], params=[],
                             condition=None):
        """Apply an operation to the output of the circuit.

        name is a string
        qargs is a list of tuples like ("q",0)
        cargs is a list of tuples like ("c",0)
        params is a list of strings that represent floats
        condition is either None or a tuple (string,int) giving (creg,value)
        """
        all_cbits = self._bits_in_condition(condition)
        all_cbits.extend(cargs)

        self._check_basis_data(name, qargs, cargs, params)
        self._check_condition(name, condition)
        self._check_bits(qargs, self.output_map, False)
        self._check_bits(all_cbits, self.output_map, True)

        self._add_op_node(name, qargs, cargs, list(map(str, params)),
                          condition)
        # Add new in-edges from predecessors of the output nodes to the
        # operation node while deleting the old in-edges of the output nodes
        # and adding new edges from the operation node to each output node
        al = [qargs, all_cbits]
        for q in itertools.chain(*al):
            ie = self.G.predecessors(self.output_map[q])
            assert len(ie) == 1, "output node has multiple in-edges"
            self.G.add_edge(ie[0], self.node_counter, name=q)
            self.G.remove_edge(ie[0], self.output_map[q])
            self.G.add_edge(self.node_counter, self.output_map[q], name=q)

    def apply_operation_front(self, name, qargs, cargs=[], params=[],
                              condition=None):
        """Apply an operation to the input of the circuit.

        name is a string
        qargs is a list of strings like "q[0]"
        cargs is a list of strings like "c[0]"
        params is a list of strings that represent floats
        condition is either None or a tuple (string,int) giving (creg,value)
        """
        all_cbits = self._bits_in_condition(condition)
        all_cbits.extend(cargs)

        self._check_basis_data(name, qargs, cargs, params)
        self._check_condition(name, condition)
        self._check_bits(qargs, self.input_map, False)
        self._check_bits(all_cbits, self.input_map, True)

        self._add_op_node(name, qargs, cargs, list(map(str, params)),
                          condition)
        # Add new out-edges to successors of the input nodes from the
        # operation node while deleting the old out-edges of the input nodes
        # and adding new edges to the operation node from each input node
        al = [qargs, all_cbits]
        for q in itertools.chain(*al):
            ie = self.G.successors(self.input_map[q])
            assert len(ie) == 1, "input node has multiple out-edges"
            self.G.add_edge(self.node_counter, ie[0], name=q)
            self.G.remove_edge(self.input_map[q], ie[0])
            self.G.add_edge(self.input_map[q], self.node_counter, name=q)

    def _make_union_basis(self, input_circuit):
        """Return a new basis map.

        The new basis is a copy of self.basis with
        new elements of input_circuit.basis added.
        input_circuit is a CircuitGraph
        """
        union_basis = copy.deepcopy(self.basis)
        for g in input_circuit.basis:
            if g not in union_basis:
                union_basis[g] = input_circuit.basis[g]
            if union_basis[g] != input_circuit.basis[g]:
                raise CircuitGraphError("incompatible basis")
        return union_basis

    def _make_union_gates(self, input_circuit):
        """Return a new gates map.

        The new gates are a copy of self.gates with
        new elements of input_circuit.gates added.
        input_circuit is a CircuitGraph

        NOTE: gates in input_circuit that are also in self must
        be *identical* to the gates in self
        """
        union_gates = copy.deepcopy(self.gates)
        for k, v in input_circuit.gates.items():
            if k not in union_gates:
                union_gates[k] = v
            if union_gates[k]["opaque"] != input_circuit.gates[k]["opaque"] or\
               union_gates[k]["n_args"] != input_circuit.gates[k]["n_args"] or\
               union_gates[k]["n_bits"] != input_circuit.gates[k]["n_bits"] or\
               union_gates[k]["args"] != input_circuit.gates[k]["args"] or\
               union_gates[k]["bits"] != input_circuit.gates[k]["bits"]:
                raise CircuitGraphError("inequivalent gate definitions for %s"
                                        % k)
            if not union_gates[k]["opaque"] and \
               union_gates[k]["body"].qasm() != \
               input_circuit.gates[k]["body"].qasm():
                raise CircuitGraphError("inequivalent gate definitions for %s"
                                        % k)
        return union_gates

    def _check_wiremap_registers(self, wire_map, keyregs, valregs,
                                 valreg=True):
        """Check that wiremap neither fragments nor leaves duplicate registers.

        1. There are no fragmented registers. A register in keyregs
        is fragmented if not all of its (qu)bits are renamed by wire_map.
        2. There are no duplicate registers. A register is duplicate if
        it appears in both self and keyregs but not in wire_map.

        wire_map is a map from (regname,idx) in keyregs to (regname,idx)
        in valregs
        keyregs is a map from register names to sizes
        valregs is a map from register names to sizes
        valreg is a Bool, if False the method ignores valregs and does not
        add regs for bits in the wire_map image that don't appear in valregs
        Return the set of regs to add to self
        """
        add_regs = set([])
        reg_frag_chk = {}
        for k, v in keyregs.items():
            reg_frag_chk[k] = {j: False for j in range(v)}
        for k in wire_map.keys():
            if k[0] in keyregs:
                reg_frag_chk[k[0]][k[1]] = True
        for k, v in reg_frag_chk.items():
            rname = ",".join(map(str, k))
            s = set(v.values())
            if len(s) == 2:
                raise CircuitGraphError("wire_map fragments reg %s" % rname)
            elif s == set([False]):
                if k in self.qregs or k in self.cregs:
                    raise CircuitGraphError("unmapped duplicate reg %s"
                                            % rname)
                else:
                    # Add registers that appear only in keyregs
                    add_regs.add((k, keyregs[k]))
            else:
                if valreg:
                    # If mapping to a register not in valregs, add it.
                    # (k,0) exists in wire_map because wire_map doesn't
                    # fragment k
                    if not wire_map[(k, 0)][0] in valregs:
                        sz = max(map(lambda x: x[1],
                                     filter(lambda x: x[0]
                                            == wire_map[(k, 0)][0],
                                            wire_map.values())))
                        add_regs.add((wire_map[(k, 0)][0], sz+1))
        return add_regs

    def _check_wiremap_validity(self, wire_map, keymap, valmap, input_circuit):
        """Check that the wiremap is consistent.

        Check that the wiremap refers to valid wires and that
        those wires have consistent types.

        wire_map is a map from (regname,idx) in keymap to (regname,idx)
        in valmap
        keymap is a map whose keys are wire_map keys
        valmap is a map whose keys are wire_map values
        input_circuit is a CircuitGraph
        """
        for k, v in wire_map.items():
            kname = ",".join(map(str, k))
            vname = ",".join(map(str, v))
            if k not in keymap:
                raise CircuitGraphError("invalid wire mapping key %s" % kname)
            if v not in valmap:
                raise CircuitGraphError("invalid wire mapping value %s"
                                        % vname)
            if input_circuit.wire_type[k] != self.wire_type[v]:
                raise CircuitGraphError("inconsistent wire_map at (%s,%s)"
                                        % (kname, vname))

    def _map_condition(self, wire_map, condition):
        """Use the wire_map dict to change the condition tuple's creg name.

        wire_map is map from wires to wires
        condition is a tuple (reg,int)
        Returns the new condition tuple
        """
        if condition is None:
            n_condition = None
        else:
            # Map the register name, using fact that registers must not be
            # fragmented by the wire_map (this must have been checked
            # elsewhere)
            bit0 = (condition[0], 0)
            n_condition = (wire_map.get(bit0, bit0)[0], condition[1])
        return n_condition

    def compose_back(self, input_circuit, wire_map={}):
        """Apply the input circuit to the output of this circuit.

        The two bases must be "compatible" or an exception occurs.
        A subset of input qubits of the input circuit are mapped
        to a subset of output qubits of this circuit.
        wire_map[input_qubit_to_input_circuit] = output_qubit_of_self
        """
        union_basis = self._make_union_basis(input_circuit)
        union_gates = self._make_union_gates(input_circuit)

        # Check the wire map for duplicate values
        if len(set(wire_map.values())) != len(wire_map):
            raise CircuitGraphError("duplicates in wire_map")

        add_qregs = self._check_wiremap_registers(wire_map,
                                                  input_circuit.qregs,
                                                  self.qregs)
        for r in add_qregs:
            self.add_qreg(r[0], r[1])

        add_cregs = self._check_wiremap_registers(wire_map,
                                                  input_circuit.cregs,
                                                  self.cregs)
        for r in add_cregs:
            self.add_creg(r[0], r[1])

        self._check_wiremap_validity(wire_map, input_circuit.input_map,
                                     self.output_map, input_circuit)

        # Compose
        self.basis = union_basis
        self.gates = union_gates
        ts = nx.topological_sort(input_circuit.G)
        for n in ts:
            nd = input_circuit.G.node[n]
            if nd["type"] == "in":
                # if in wire_map, get new name, else use existing name
                m_name = wire_map.get(nd["name"], nd["name"])
                # the mapped wire should already exist
                assert m_name in self.output_map, \
                 "wire (%s,%d) not in self" % (m_name[0], m_name[1])
                assert nd["name"] in input_circuit.wire_type, \
                 "inconsistent wire_type for (%s,%d) in input_circuit" \
                 % (nd["name"][0], nd["name"][1])
            elif nd["type"] == "out":
                # ignore output nodes
                pass
            elif nd["type"] == "op":
                condition = self._map_condition(wire_map, nd["condition"])
                self._check_condition(nd["name"], condition)
                m_qargs = list(map(lambda x: wire_map.get(x, x), nd["qargs"]))
                m_cargs = list(map(lambda x: wire_map.get(x, x), nd["cargs"]))
                self.apply_operation_back(nd["name"], m_qargs, m_cargs,
                                          nd["params"], condition)
            else:
                assert False, "bad node type %s" % nd["type"]

    def compose_front(self, input_circuit, wire_map={}):
        """Apply the input circuit to the input of this circuit.

        The two bases must be "compatible" or an exception occurs.
        A subset of output qubits of the input circuit are mapped
        to a subset of input qubits of
        this circuit.
        """
        union_basis = self._make_union_basis(input_circuit)
        union_gates = self._make_union_gates(input_circuit)

        # Check the wire map
        if len(set(wire_map.values())) != len(wire_map):
            raise CircuitGraphError("duplicates in wire_map")

        add_qregs = self._check_wiremap_registers(wire_map,
                                                  input_circuit.qregs,
                                                  self.qregs)
        for r in add_qregs:
            self.add_qreg(r[0], r[1])

        add_cregs = self._check_wiremap_registers(wire_map,
                                                  input_circuit.cregs,
                                                  self.cregs)
        for r in add_cregs:
            self.add_creg(r[0], r[1])

        self._check_wiremap_validity(wire_map, input_circuit.output_map,
                                     self.input_map, input_circuit)

        # Compose
        self.basis = union_basis
        self.gates = union_gates
        ts = nx.topological_sort(input_circuit.G, reverse=True)
        for n in ts:
            nd = input_circuit.G.node[n]
            if nd["type"] == "out":
                # if in wire_map, get new name, else use existing name
                m_name = wire_map.get(nd["name"], nd["name"])
                # the mapped wire should already exist
                assert m_name in self.input_map, \
                 "wire (%s,%d) not in self" % (m_name[0], m_name[1])
                assert nd["name"] in input_circuit.wire_type, \
                 "inconsistent wire_type for (%s,%d) in input_circuit" \
                 % (nd["name"][0], nd["name"][1])
            elif nd["type"] == "in":
                # ignore input nodes
                pass
            elif nd["type"] == "op":
                condition = self._map_condition(wire_map, nd["condition"])
                self._check_condition(nd["name"], condition)
                m_qargs = list(map(lambda x: wire_map.get(x, x), nd["qargs"]))
                m_cargs = list(map(lambda x: wire_map.get(x, x), nd["cargs"]))
                self.apply_operation_front(nd["name"], m_qargs, m_cargs,
                                           nd["params"], condition)
            else:
                assert False, "bad node type %s" % nd["type"]

    def size(self):
        """Return the number of operations."""
        return self.G.order() - 2*len(self.wire_type)

    def depth(self):
        """Return the circuit depth."""
        assert nx.is_directed_acyclic_graph(self.G), "not a DAG"
        return nx.dag_longest_path_length(self.G)-1

    def width(self):
        """Return the total number of qubits used by the circuit."""
        return len(self.wire_type) - self.num_cbits()

    def num_cbits(self):
        """Return the total number of bits used by the circuit."""
        return list(self.wire_type.values()).count(True)

    def num_tensor_factors(self):
        """Compute how many components the circuit can decompose into."""
        return nx.number_weakly_connected_components(self.G)

    def _gate_string(self, name):
        """Return a QASM string for the named gate."""
        out = ""
        if self.gates[name]["opaque"]:
            out = "opaque " + name
        else:
            out = "gate " + name
        if self.gates[name]["n_args"] > 0:
            out += "(" + ",".join(self.gates[name]["args"]) + ")"
        out += " " + ",".join(self.gates[name]["bits"])
        if self.gates[name]["opaque"]:
            out += ";"
        else:
            out += "\n{\n" + self.gates[name]["body"].qasm() + "}"
        return out

    def qasm(self, qeflag=False):
        """Return a string containing QASM for this circuit.

        if qeflag is True, add a line to include "qelib1.inc"
        and only generate gate code for gates not in qelib1.
        """
        printed_gates = []
        out = "IBMQASM 2.0;\n"
        if qeflag:
            out += "include \"qelib1.inc\";\n"
        for k, v in sorted(self.qregs.items()):
            out += "qreg %s[%d];\n" % (k, v)
        for k, v in sorted(self.cregs.items()):
            out += "creg %s[%d];\n" % (k, v)
        omit = ["U", "CX", "measure", "reset", "barrier"]
        if qeflag:
            qelib = ["u3", "u2", "u1", "cx", "id", "x", "y", "z", "h",
                     "s", "sdg", "t", "tdg", "cz", "cy", "ccx", "cu1", "cu3"]
            omit.extend(qelib)
            printed_gates.extend(qelib)
        for k in self.basis.keys():
            if k not in omit:
                if not self.gates[k]["opaque"]:
                    calls = self.gates[k]["body"].calls()
                    for c in calls:
                        if c not in printed_gates:
                            out += self._gate_string(c) + "\n"
                            printed_gates.append(c)
                if k not in printed_gates:
                    out += self._gate_string(k) + "\n"
                    printed_gates.append(k)
        ts = nx.topological_sort(self.G)
        for n in ts:
            nd = self.G.node[n]
            if nd["type"] == "op":
                if nd["condition"] is not None:
                    out += "if(%s==%d) " \
                           % (nd["condition"][0], nd["condition"][1])
                if len(nd["cargs"]) == 0:
                    nm = nd["name"]
                    qarg = ",".join(map(lambda x: "%s[%d]" % (x[0], x[1]),
                                        nd["qargs"]))
                    if len(nd["params"]) > 0:
                        param = ",".join(nd["params"])
                        out += "%s(%s) %s;\n" % (nm, param, qarg)
                    else:
                        out += "%s %s;\n" % (nm, qarg)
                else:
                    if nd["name"] == "measure":
                        assert len(nd["cargs"]) == 1 and \
                               len(nd["qargs"]) == 1 and \
                               len(nd["params"]) == 0, "bad node data"
                        out += "measure %s[%d] -> %s[%d];\n" \
                               % (nd["qargs"][0][0],
                                  nd["qargs"][0][1],
                                  nd["cargs"][0][0],
                                  nd["cargs"][0][1])
                    else:
                        assert False, "bad node data"
        return out

    def _check_wires_list(self, wires, name, input_circuit):
        """Check that a list of wires satisfies some conditions.

        The wires give an order for (qu)bits in the input circuit
        that is replacing the named operation.
        - no duplicate names
        - correct length for named operation
        - elements are wires of input_circuit
        Raises an exception otherwise.
        """
        if len(set(wires)) != len(wires):
            raise CircuitGraphError("duplicate wires")

        wire_tot = self.basis[name][0] + self.basis[name][1]
        if len(wires) != wire_tot:
            raise CircuitGraphError("expected %d wires, got %d"
                                    % (wire_tot, len(wires)))

        for w in wires:
            if w not in input_circuit.wire_type:
                raise CircuitGraphError("wire (%s,%d) not in input circuit"
                                        % (w[0], w[1]))

    def _make_pred_succ_maps(self, n):
        """Return predecessor and successor dictionaries.

        These map from wire names to predecessor and successor
        nodes for the operation node n in self.G.
        """
        pred_map = {e[2]['name']: e[0] for e in
                    self.G.in_edges_iter(nbunch=n, data=True)}
        succ_map = {e[2]['name']: e[1] for e in
                    self.G.out_edges_iter(nbunch=n, data=True)}
        return pred_map, succ_map

    def _full_pred_succ_maps(self, pred_map, succ_map, input_circuit,
                             wire_map):
        """Map all wires of the input circuit.

        Map all wires of the input circuit to predecessor and
        successor nodes in self, keyed on wires in self.

        pred_map, succ_map dicts come from _make_pred_succ_maps
        input_circuit is the input circuit
        wire_map is the wire map from wires of input_circuit to wires of self
        returns full_pred_map, full_succ_map
        """
        full_pred_map = {}
        full_succ_map = {}
        for w in input_circuit.input_map:
            # If w is wire mapped, find the corresponding predecessor
            # of the node
            if w in wire_map:
                full_pred_map[wire_map[w]] = pred_map[wire_map[w]]
                full_succ_map[wire_map[w]] = succ_map[wire_map[w]]
            else:
                # Otherwise, use the corresponding output nodes of self
                # and compute the predecessor.
                full_succ_map[w] = self.output_map[w]
                full_pred_map[w] = self.G.predecessors(self.output_map[w])[0]
                assert len(self.G.predecessors(self.output_map[w])) == 1,\
                 "too many predecessors for (%s,%d) output node" % (w[0], w[1])
        return full_pred_map, full_succ_map

    def substitute_circuit_all(self, name, input_circuit, wires=[]):
        """Replace every occurrence of named operation with input_circuit."""
        if name not in self.basis:
            raise CircuitGraphError("%s is not in the list of basis operations"
                                    % name)

        self._check_wires_list(wires, name, input_circuit)
        union_basis = self._make_union_basis(input_circuit)
        union_gates = self._make_union_gates(input_circuit)

        # Create a proxy wire_map to identify fragments and duplicates
        # and determine what registers need to be added to self
        proxy_map = {w: ("", 0) for w in wires}
        add_qregs = self._check_wiremap_registers(proxy_map,
                                                  input_circuit.qregs,
                                                  {}, False)
        for r in add_qregs:
            self.add_qreg(r[0], r[1])

        add_cregs = self._check_wiremap_registers(proxy_map,
                                                  input_circuit.cregs,
                                                  {}, False)
        for r in add_cregs:
            self.add_creg(r[0], r[1])

        # Iterate through the nodes of self and replace the selected nodes
        # by iterating through the input_circuit, constructing and
        # checking the validity of the wire_map for each replacement
        # NOTE: We do not replace conditioned gates. One way to implement
        #       this later is to add or update the conditions of each gate
        #       that we add from the input_circuit.
        self.basis = union_basis
        self.gates = union_gates
        ts = nx.topological_sort(self.G)
        for n in ts:
            nd = self.G.node[n]
            if nd["type"] == "op" and nd["name"] == name:
                if nd["condition"] is None:
                    wire_map = {k: v for k, v in zip(wires,
                                [i for s in [nd["qargs"], nd["cargs"]]
                                 for i in s])}
                    self._check_wiremap_validity(wire_map, wires,
                                                 self.input_map, input_circuit)
                    pred_map, succ_map = self._make_pred_succ_maps(n)
                    full_pred_map, full_succ_map = \
                        self._full_pred_succ_maps(pred_map, succ_map,
                                                  input_circuit, wire_map)
                    # Now that we know the connections, delete node
                    self.G.remove_node(n)
                    # Iterate over nodes of input_circuit
                    tsin = nx.topological_sort(input_circuit.G)
                    for m in tsin:
                        md = input_circuit.G.node[m]
                        if md["type"] == "op":
                            # Insert a new node
                            condition = self._map_condition(wire_map,
                                                            md["condition"])
                            m_qargs = list(map(lambda x: wire_map.get(x, x),
                                               md["qargs"]))
                            m_cargs = list(map(lambda x: wire_map.get(x, x),
                                               md["cargs"]))
                            self._add_op_node(md["name"], m_qargs, m_cargs,
                                              md["params"], condition)
                            # Add edges from predecessor nodes to new node
                            # and update predecessor nodes that change
                            all_cbits = self._bits_in_condition(condition)
                            all_cbits.extend(m_cargs)
                            al = [m_qargs, all_cbits]
                            for q in itertools.chain(*al):
                                self.G.add_edge(full_pred_map[q],
                                                self.node_counter, name=q)
                                full_pred_map[q] = copy.copy(self.node_counter)
                    # Connect all predecessors and successors, and remove
                    # residual edges between input and output nodes
                    for w in full_pred_map.keys():
                        self.G.add_edge(full_pred_map[w], full_succ_map[w],
                                        name=w)
                        o_pred = self.G.predecessors(self.output_map[w])
                        if len(o_pred) > 1:
                            assert len(o_pred) == 2, \
                                   "expected 2 predecessors here"
                            p = list(filter(lambda x: x != full_pred_map[w],
                                            o_pred))
                            assert len(p) == 1, \
                                   "expected 1 predecessor to pass filter"
                            self.G.remove_edge(p[0], self.output_map[w])

    def substitute_circuit_one(self, node, input_circuit, wires=[]):
        """Replace one node with input_circuit.

        node is a reference to a node of self.G of type "op"
        input_circuit is a CircuitGraph
        """
        nd = self.G.node[node]

        # TODO: reuse common code in substitute_circuit_one and _all

        name = nd["name"]
        self._check_wires_list(wires, name, input_circuit)
        union_basis = self._make_union_basis(input_circuit)
        union_gates = self._make_union_gates(input_circuit)

        # Create a proxy wire_map to identify fragments and duplicates
        # and determine what registers need to be added to self
        proxy_map = {w: ("", 0) for w in wires}
        add_qregs = self._check_wiremap_registers(proxy_map,
                                                  input_circuit.qregs,
                                                  {}, False)
        for r in add_qregs:
            self.add_qreg(r[0], r[1])

        add_cregs = self._check_wiremap_registers(proxy_map,
                                                  input_circuit.cregs,
                                                  {}, False)
        for r in add_cregs:
            self.add_creg(r[0], r[1])

        # Replace the node by iterating through the input_circuit.
        # Constructing and checking the validity of the wire_map.
        # NOTE: We do not replace conditioned gates. One way to implement
        #       later is to add or update the conditions of each gate we add
        #       from the input_circuit.
        self.basis = union_basis
        self.gates = union_gates

        if nd["type"] != "op":
            raise CircuitGraphError("expected node type \"op\", got %s"
                                    % nd["type"])

        if nd["condition"] is None:
            wire_map = {k: v for k, v in zip(wires,
                                             [i for s in [nd["qargs"],
                                                          nd["cargs"]]
                                              for i in s])}
            self._check_wiremap_validity(wire_map, wires,
                                         self.input_map, input_circuit)
            pred_map, succ_map = self._make_pred_succ_maps(node)
            full_pred_map, full_succ_map = \
                self._full_pred_succ_maps(pred_map, succ_map,
                                          input_circuit, wire_map)
            # Now that we know the connections, delete node
            self.G.remove_node(node)
            # Iterate over nodes of input_circuit
            tsin = nx.topological_sort(input_circuit.G)
            for m in tsin:
                md = input_circuit.G.node[m]
                if md["type"] == "op":
                    # Insert a new node
                    condition = self._map_condition(wire_map, md["condition"])
                    m_qargs = list(map(lambda x: wire_map.get(x, x),
                                       md["qargs"]))
                    m_cargs = list(map(lambda x: wire_map.get(x, x),
                                       md["cargs"]))
                    self._add_op_node(md["name"], m_qargs, m_cargs,
                                      md["params"], condition)
                    # Add edges from predecessor nodes to new node
                    # and update predecessor nodes that change
                    all_cbits = self._bits_in_condition(condition)
                    all_cbits.extend(m_cargs)
                    al = [m_qargs, all_cbits]
                    for q in itertools.chain(*al):
                        self.G.add_edge(full_pred_map[q], self.node_counter,
                                        name=q)
                        full_pred_map[q] = copy.copy(self.node_counter)
            # Connect all predecessors and successors, and remove
            # residual edges between input and output nodes
            for w in full_pred_map.keys():
                self.G.add_edge(full_pred_map[w], full_succ_map[w], name=w)
                o_pred = self.G.predecessors(self.output_map[w])
                if len(o_pred) > 1:
                    assert len(o_pred) == 2, "expected 2 predecessors here"
                    p = list(filter(lambda x: x != full_pred_map[w], o_pred))
                    assert len(p) == 1, "expected 1 predecessor to pass filter"
                    self.G.remove_edge(p[0], self.output_map[w])

    def get_named_nodes(self, name):
        """Return a list of "op" nodes with the given name."""
        nlist = []
        if name not in self.basis:
            raise CircuitGraphError("%s is not in the list of basis operations"
                                    % name)

        # Iterate through the nodes of self in topological order
        ts = nx.topological_sort(self.G)
        for n in ts:
            nd = self.G.node[n]
            if nd["type"] == "op" and nd["name"] == name:
                nlist.append(n)
        return nlist

    def _remove_op_node(self, n):
        """Remove an operation node n.

        Add edges from predecessors to successors.
        """
        pred_map, succ_map = self._make_pred_succ_maps(n)
        self.G.remove_node(n)
        for w in pred_map.keys():
            self.G.add_edge(pred_map[w], succ_map[w], name=w)

    def remove_ancestors_of(self, node):
        """Remove all of the ancestor operation nodes of node."""
        anc = nx.ancestors(self.G, node)
        # TODO: probably better to do all at once using
        # G.remove_nodes_from; same for related functions ...
        for n in anc:
            nd = self.G.node[n]
            if nd["type"] == "op":
                self._remove_op_node(n)

    def remove_descendants_of(self, node):
        """Remove all of the descendant operation nodes of node."""
        dec = nx.descendants(self.G, node)
        for n in dec:
            nd = self.G.node[n]
            if nd["type"] == "op":
                self._remove_op_node(n)

    def remove_nonancestors_of(self, node):
        """Remove all of the non-ancestors operation nodes of node."""
        anc = nx.ancestors(self.G, node)
        comp = list(set(self.G.nodes()) - set(anc))
        for n in comp:
            nd = self.G.node[n]
            if nd["type"] == "op":
                self._remove_op_node(n)

    def remove_nondescendants_of(self, node):
        """Remove all of the non-descendants operation nodes of node."""
        dec = nx.descendants(self.G, node)
        comp = list(set(self.G.nodes()) - set(dec))
        for n in comp:
            nd = self.G.node[n]
            if nd["type"] == "op":
                self._remove_op_node(n)

    def layers(self):
        """Return a list of layers for all d layers of this circuit.

        A layer is a circuit whose gates act on disjoint qubits, i.e.
        a layer has depth 1. The total number of layers equals the
        circuit depth d. The layers are indexed from 0 to d-1 with the
        earliest layer at index 0. The layers are constructed using a
        greedy algorithm. Each returned layer is a dict containing
        {"graph": circuit graph, "partition": list of qubit lists}.
        """
        layers_list = []
        # node_map contains an input node or previous layer node for
        # each wire in the circuit.
        node_map = copy.deepcopy(self.input_map)
        # wires_with_ops_remaining is a set of wire names that have
        # operations we still need to assign to layers
        wires_with_ops_remaining = set(self.input_map.keys())
        while wires_with_ops_remaining:
            # Create a new circuit graph and populate with regs and basis
            new_layer = CircuitGraph()
            for k, v in self.qregs.items():
                new_layer.add_qreg(k, v)
            for k, v in self.cregs.items():
                new_layer.add_creg(k, v)
            new_layer.basis = copy.deepcopy(self.basis)
            new_layer.gates = copy.deepcopy(self.gates)
            # Save the support of each operation we add to the layer
            support_list = []
            # Determine what operations to add in this layer
            # ops_touched is a map from operation nodes touched in this
            # iteration to the set of their unvisited input wires. When all
            # of the inputs of a touched node are visited, the node is a
            # foreground node we can add to the current layer.
            ops_touched = {}
            wires_loop = list(wires_with_ops_remaining)
            for w in wires_loop:
                oe = list(filter(lambda x: x[2]["name"] == w,
                                 self.G.out_edges(nbunch=[node_map[w]],
                                                  data=True)))
                assert len(oe) == 1, "should only be one out-edge per (qu)bit"
                nxt_nd_idx = oe[0][1]
                nxt_nd = self.G.node[nxt_nd_idx]
                # If we reach an output node, we are done with this wire.
                if nxt_nd["type"] == "out":
                    wires_with_ops_remaining.remove(w)
                # Otherwise, we are somewhere inside the circuit
                elif nxt_nd["type"] == "op":
                    # Operation data
                    qa = copy.copy(nxt_nd["qargs"])
                    ca = copy.copy(nxt_nd["cargs"])
                    pa = copy.copy(nxt_nd["params"])
                    co = copy.copy(nxt_nd["condition"])
                    cob = self._bits_in_condition(co)
                    # First time we see an operation, add to ops_touched
                    if nxt_nd_idx not in ops_touched:
                        ops_touched[nxt_nd_idx] = set(qa) | set(ca) | set(cob)
                    # Mark inputs visited by deleting from set
                    # NOTE: expect trouble with if(c==1) measure q -> c;
                    assert w in ops_touched[nxt_nd_idx], "expected wire"
                    ops_touched[nxt_nd_idx].remove(w)
                    # Node becomes "foreground" if set becomes empty,
                    # i.e. every input is available for this operation
                    if not ops_touched[nxt_nd_idx]:
                        # Add node to new_layer
                        new_layer.apply_operation_back(nxt_nd["name"],
                                                       qa, ca, pa, co)
                        # Update node_map to point to this op
                        for v in itertools.chain(qa, ca, cob):
                            node_map[v] = nxt_nd_idx
                        # Add operation to partition
                        support_list.append(list(set(qa) | set(ca) | set(cob)))
            if support_list:
                l_dict = {"graph": new_layer, "partition": support_list}
                layers_list.append(l_dict)
            else:
                assert not wires_with_ops_remaining, "not finished but empty?"
        return layers_list
