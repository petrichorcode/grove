##############################################################################
# Copyright 2016-2017 Rigetti Computing
#
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.
##############################################################################

"""
Module for the Simon's Algorithm.
For more information, see

- https://courses.cs.washington.edu/courses/cse599/01wi/papers/simon_qc.pdf
- http://lapastillaroja.net/wp-content/uploads/2016/09/Intro_to_QC_Vol_1_Loceff.pdf
- https://cs.uwaterloo.ca/~watrous/CPSC519/LectureNotes/06.pdf
"""

import numpy as np
import numpy.random as rd
import pyquil.quil as pq
from pyquil.gates import H
import grove.alpha.simon.utils as u
from collections import defaultdict


def create_periodic_1to1_bitmap(mask):
    """
    A helper to create a bit map function for a given mask. E.g. for a mask :math:`m = 10` the
    return would is a dictionary:

    >>> create_periodic_1to1_bitmap('10')
    ... {
    ...     '00': '10',
    ...     '01': '11',
    ...     '10': '00',
    ...     '11': '01'
    ... }

    :param mask: binary mask as a string of 0's and 1's
    :return: dictionary containing a mapping of all possible bit strings of the same size a the mask
    and their mapped bit-string value

    :rtype: Dict[String, String]
    """
    n_bits = len(mask)
    form_string = "{0:0" + str(n_bits) + "b}"
    dct = {}
    for idx in range(2**n_bits):
        bit_string = form_string.format(idx)
        dct[bit_string] = u.bitwise_xor(bit_string, mask)
    return dct


def create_valid_2to1_bitmap(mask):
    """
    A helper to create a valid 2-to-1 function with a given mask. This can be used to create a valid
    input to Simon's algorithm.

    A 2-to-1 function is boolean function with the property :math:`f(x) = f(x \oplus m)` where
    :math:`m` is a bit mask and :math:`\oplus` denotes the bit wise XOR operation. An example of
    such a function is the truth-table

    ==== ====
     x   f(x)
    ==== ====
    000  101
    001  010
    010  000
    011  110
    100  000
    101  110
    110  101
    111  010

    Note that, e.g. both `000` and `110` map to the same value `101` and
    :math:`000 \oplus 110 = 110`. The same holds true for other pairs.

    :param mask: mask input that defines the periodicity of f
    :return: dictionary containing the truth table of a valid 2-to-1 boolean function
    :rtype: Dict[String, String]
    """
    bm = create_periodic_1to1_bitmap(mask)
    n_samples = int(len(list(bm.keys())) / 2)
    list_of_half_size = list(rd.choice(list(bm.keys()), replace=False, size=n_samples))

    list_of_tup = sorted([(k, v) for k, v in bm.items()], key=lambda x: x[0])

    dct = {}
    cnt = 0
    while cnt < n_samples:
        tup = list_of_tup[cnt]
        val = list_of_half_size[cnt]
        dct[tup[0]] = val
        dct[tup[1]] = val
        cnt += 1

    return dct


class Simon(object):
    """
    `Simon's algorithm`_ is amongst the earliest algorithms providing an exponential speedup of a
    computation using a Quantum Computer vs. a Classical Computer.

    This class contains an implementation of Simon's algorithm with pyQuil and can be run on
    hardware provided by Rigetti Computing, Inc.

    .. _`Simon's algorithm`:https://courses.cs.washington.edu/courses/cse599/01wi/papers/simon_qc.pdf
    """

    def __init__(self):
        self.unitary_function_mapping = None
        self.n_qubits = None
        self.n_ancillas = None
        self._qubits = None
        self.log_qubits = None
        self.ancillas = None
        self.simon_circuit = None
        self.oracle_circuit = None
        self._dict_of_linearly_indep_bit_vectors = {}
        self.mask = None
        self.bit_map = None
        self.classical_register = None

    def _construct_simon_circuit(self):
        """
        Implementation of the quantum portion of Simon's Algorithm.

        Given a list of input qubits,
        all initially in the :math:`\\vert 0\\rangle` state,
        create a program that applies the Hadamard-Walsh transform the qubits
        before and after going through the oracle.

        :param Program oracle: Program representing unitary application of function
        :param list(int) qubits: List of qubits that enter as the input
                            :math:`\\vert x \\rangle`.
        :return: A program corresponding to the desired instance of
                 Simon's Algorithm.
        :rtype: Program
        """
        p = pq.Program()

        oracle_name = "SIMON_ORACLE"
        p.defgate(oracle_name, self.unitary_function_mapping)

        p.inst([H(i) for i in self.log_qubits])
        p.inst(tuple([oracle_name] + sorted(self._qubits, reverse=True)))
        p.inst([H(i) for i in self.log_qubits])
        return p

    def _init_attr(self, bitstring_map):
        """
        Acts instead of __init__ method to instantiate the necessary Simon Object state.

        :param bitstring_map: truth-table of the input bitstring map in dictionary format
        :return: None
        """
        self.bit_map = bitstring_map
        self.n_qubits = len(list(bitstring_map.keys())[0])
        self.n_ancillas = self.n_qubits
        self._qubits = list(range(self.n_qubits + self.n_ancillas))
        self.log_qubits = self._qubits[:self.n_qubits]
        self.ancillas = self._qubits[self.n_qubits:]
        self.classical_register = np.asarray(list(range(self.n_qubits + self.n_ancillas)))
        self.unitary_function_mapping, _ = self._compute_unitary_oracle_matrix(bitstring_map)
        self.simon_circuit = self._construct_simon_circuit()
        self._dict_of_linearly_indep_bit_vectors = {}
        self.mask = None
        self.classical_register = np.asarray(list(range(self.n_qubits + self.n_ancillas)))

    @staticmethod
    def _compute_unitary_oracle_matrix(bitstring_map):
        """
        Computes the unitary matrix that encodes the orcale function for Simon's algorithm

        :param bitstring_map: truth-table of the input bitstring map in dictionary format
        :return: a dense matrix containing the permutation of the bit strings and a dictionary
        containing the indices of the non-zero elements of the computed permutation matrix as
        key-value-pairs
        :rtype: Tuple[2darray, Dict[String, String]]
        """
        n_bits = len(list(bitstring_map.keys())[0])
        ufunc = np.zeros(shape=(2 ** (2 * n_bits), 2 ** (2 * n_bits)))
        dct = defaultdict(dict)
        for b in range(2**n_bits):
            pad_str = np.binary_repr(b, n_bits)
            for k, v in bitstring_map.items():
                dct[pad_str + k] = u.bitwise_xor(pad_str, v) + k
                i, j = int(pad_str+k, 2), int(u.bitwise_xor(pad_str, v) + k, 2)
                ufunc[i, j] = 1
        return ufunc, dct

    def find_mask(self, cxn, bitstring_map):
        """
        Runs Simon'mask_array algorithm to find the mask.

        :param JobConnection cxn: the connection to the Rigetti cloud to run pyQuil programs
        :param Dict[String, String] bitstring_map: a truth table describing the boolean function,
        whose period is  to be found.

        :return: Returns the mask (period) of the bitstring map or raises and Exception if the mask
        cannot be found.
        """
        if not isinstance(bitstring_map, dict):
            raise ValueError("Bitstring map needs to be a map from bitstring to bitstring")
        self._init_attr(bitstring_map)

        # create the samples of linearly independent bit-vectors
        self._sample_independent_bit_vectors(cxn)
        # try to invert the mask and check validity
        self._invert_mask_equation()

        if self._check_mask_correct():
            return self.mask
        else:
            raise Exception("No valid mask found")

    def _sample_independent_bit_vectors(self, cxn):
        """This method samples :math:`n-1` linearly independent vectors using the Simon Circuit.
        It attempts to put the sampled bitstring into a dictionary and only terminates once the
        dictionary contains :math:`n-1` samples




        :param cxn: Connection object to the Quantum Engine (QVM, QPU)
        :return: None
        """
        while len(self._dict_of_linearly_indep_bit_vectors) < self.n_qubits - 1:
            z = np.array(cxn.run_and_measure(self.simon_circuit, self.log_qubits)[0], dtype=int)
            self._add_to_dict_of_indep_bit_vectors(z.tolist())

    def _invert_mask_equation(self):
        """
        This method tries to infer the bit mask of the input function from the sampled :math:`n-1`
        linearly independent bit vectors.

        It first finds the missing provenance in the collection of sampled bit vectors, then
        constructs a matrix in upper-triangular (row-echelon) form and finally uses backsubstitution
        over :math:`GF(2)` to find a solution to the equation

            :math:`\\mathbf{\\mathit{W}}\\mathbf{m}=\\mathbf{a}`

        where :math:`a` represents the bit vector of missing provenance, :math:`\mathbf{m}` is the
        mask to be found and :math:`\\mathbf{\\mathit{W}}` is the constructed upper-triangular
        matrix.

        :return: None
        """
        missing_prov = self._add_missing_provenance_vector()
        upper_triangular_matrix = np.asarray(
            [tup[1] for tup in sorted(zip(self._dict_of_linearly_indep_bit_vectors.keys(),
                                          self._dict_of_linearly_indep_bit_vectors.values()),
                                      key=lambda x: x[0])])

        provenance_unit = np.zeros(shape=(self.n_qubits,), dtype=int)
        provenance_unit[missing_prov] = 1

        self.mask = u.binary_back_substitute(upper_triangular_matrix, provenance_unit).tolist()

    def _add_to_dict_of_indep_bit_vectors(self, z):
        """
        This method adds a bit-vector z to the dictionary of independent vectors. It checks the
        provenance (most significant bit) of the vector and only adds it to the dictionary if the
        provenance is not yet found in the dictionary. This guarantees that we can write up a
        resulting matrix in upper-triangular form which by virtue of its form is invertible

        :param z: array containing the bit-vector
        :return: None
        """
        if all(np.asarray(z) == 0) or all(np.asarray(z) == 1):
            return
        msb_z = u.most_significant_bit(z)

        # try to add bitstring z to samples dictionary directly
        if msb_z not in self._dict_of_linearly_indep_bit_vectors.keys():
            self._dict_of_linearly_indep_bit_vectors[msb_z] = z
        # if we have a conflict with the provenance of a sample try to create
        # bit-wise XOR vector (guaranteed to be orthogonal to the conflict) and add
        # that to the samples.
        # Bail if this doesn't work and continue sampling.
        else:
            conflict_z = self._dict_of_linearly_indep_bit_vectors[msb_z]
            not_z = [conflict_z[idx] ^ z[idx] for idx in range(len(z))]
            if all(np.asarray(not_z) == 0):
                return
            msb_not_z = u.most_significant_bit(not_z)
            if msb_not_z not in self._dict_of_linearly_indep_bit_vectors.keys():
                self._dict_of_linearly_indep_bit_vectors[msb_not_z] = not_z

    def _add_missing_provenance_vector(self):
        """
        Finds the missing provenance value in the collection of :math:`n-1` linearly independent
        bit vectors and adds a unit vector corresponding to the missing provenance to the collection

        :return: Missing provenance value as int
        :rtype: Int
        """
        missing_prov = None
        for idx in range(self.n_qubits):
            if idx not in self._dict_of_linearly_indep_bit_vectors.keys():
                missing_prov = idx

        if missing_prov is None:
            raise ValueError("Expected a missing provenance, but didn't find one.")

        augment_vec = np.zeros(shape=(self.n_qubits,))
        augment_vec[missing_prov] = 1
        self._dict_of_linearly_indep_bit_vectors[missing_prov] = augment_vec.astype(int).tolist()
        return missing_prov

    def _check_mask_correct(self):
        """
        Checks if a given mask correctly reproduces the function that was provided to the Simon
        algorithm. This can be done in :math:`O(n)` as it is a simple list traversal.

        :return: True if mask reproduces the input function
        """
        mask_str = ''.join([str(b) for b in self.mask])
        return all([self.bit_map[k] == self.bit_map[u.bitwise_xor(k, mask_str)]
                    for k in self.bit_map.keys()])
