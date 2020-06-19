import os
import numpy as np
import copy
from scipy.linalg import eigh, eigvalsh
from scipy.sparse import csr_matrix
from scipy.io import netcdf_file
from collections import defaultdict
#from tbmodels import Model
from ase.io import read
from ase.atoms import Atoms
from TB2J.utils import auto_assign_basis_name
from functools import lru_cache
from TB2J.wannier import parse_ham, parse_xyz



class AbstractTB():
    def __init__(self):
        raise NotImplementedError()

    @property
    def nspin(self):
        raise NotImplementedError()

    @property
    def norb(self):
        raise NotImplementedError()

    @property
    def nbasis(self):
        return self.nspin * self.norb

    @property
    def ndim(self):
        raise NotImplementedError()

    @property
    def xcart(self):
        raise NotImplementedError()

    @property
    def xred(self):
        raise NotImplementedError()

    @property
    def onsite_energies(self):
        raise NotImplementedError()

    def get_onsite_SOC(self):
        raise NotImplementedError()

    def gen_ham(self, kpts, convention=2):
        raise NotImplementedError()

    def eigen(self, k, convention=2):
        """
        calculate eigen for one k pooint
        :param k: kpoint.
        """
        evals, evecs = np.linalg.eigh(
            self.gen_ham(tuple(k), convention=convention))
        return evals, evecs

    def HS_and_eigen(self, kpts, convention=2):
        """
        calculate eigens for all kpoints. 
        :param kpts: list of k points.
        """
        nk = len(kpts)
        hams = np.zeros((nk, self.nbasis, self.nbasis), dtype=complex)
        evals = np.zeros((nk, self.nbasis), dtype=float)
        evecs = np.zeros((nk, self.nbasis, self.nbasis), dtype=complex)
        for ik, k in enumerate(kpts):
            hams[ik]=self.gen_ham(k, convention=convention)
            evals[ik], evecs[ik] = eigh(ham[ik])
        return hams, None, evals, evecs




class MyTB():
    def __init__(self,
                 nbasis,
                 data=None,
                 positions=None,
                 sparse=False,
                 ndim=3,
                 nspin=1,
                 double_site_energy=2.0):
        """
        :param nbasis: number of basis.
        :param data: a dictionary of {R: matrix}. R is a tuple, matrix is a nbasis*nbasis matrix.
        :param positions: reduced positions.
        :param sparse: Bool, whether to use a sparse matrix.
        :param ndim: number of dimensions.
        :param nspin: number of spins.
        """

        if data is not None:
            self.data = data
        else:
            self.data = defaultdict(lambda: np.zeros((nbasis, nbasis),
                                                     dtype=complex))
        self._nbasis = nbasis
        self._nspin = nspin
        self._norb = nbasis // nspin
        self._ndim = ndim
        if positions is None:
            self._positions = np.zeros((nbasis, self.ndim))
        else:
            self._positions = positions
        self.prepare_phase_rjri()
        self.sparse = sparse
        self.double_site_energy = double_site_energy
        if sparse:
            self._matrix = csr_matrix
        self.atoms = None
        self.R2kfactor=2.0j*np.pi
        self.k2Rfactor=-2.0j*np.pi

    def set_atoms(self, atoms):
        self.atoms = atoms

    @property
    def nspin(self):
        return self._nspin

    @property
    def norb(self):
        """
        norb: number of orbitals, if spin/spinor, norb=nbasis/2
        """
        return self._norb

    @property
    def nbasis(self):
        return self._nbasis

    @property
    def ndim(self):
        return self._ndim

    @property
    def xcart(self):
        raise NotImplementedError()

    @property
    def xred(self):
        return self._positions

    @property
    def positions(self):
        return self._positions

    @property
    def onsite_energies(self):
        return self.data[(0, 0, 0)].diagonal() * 2

    @property
    def hoppings(self):
        """
        The hopping parameters, not including any onsite energy.
        """
        data = copy.deepcopy(self.data)
        np.fill_diagonal(data[(0, 0, 0)], 0.0)
        return data

    @staticmethod
    def read_from_wannier_dir(path, prefix, posfile='POSCAR', nls=True):
        """
        read tight binding model from a wannier function directory. 
        :param path: path
        :param prefix: prefix to the wannier files, often wannier90, or wannier90_up, or wannier90_dn for vasp.
        """
        #tbmodel = Model.from_wannier_folder(
        #    folder=path, prefix=prefix)
        #m = MyTB.from_tbmodel(tbmodel)
        nbasis, data=parse_ham(fname=os.path.join(path, prefix+'_hr.dat'))
        xcart, _, _=parse_xyz(fname=os.path.join(path, prefix+'_centres.xyz'))
        atoms = read(os.path.join(path, posfile))
        cell=atoms.get_cell()
        xred=cell.scaled_positions(xcart)
        ind, positions = auto_assign_basis_name(xred, atoms)
        m=MyTB(nbasis=nbasis, data=data, positions=xred)
        nm = m.shift_position(positions)
        nm.set_atoms(atoms)
        return nm

    @lru_cache(maxsize=1000)
    def gen_ham(self, k, convention=2):
        """
        generate hamiltonian matrix at k point.
        H_k( i, j)=\sum_R H_R(i, j)^phase.
        There are two conventions,         
        first:
        phase =e^{ik(R+rj-ri)}. often better used for berry phase.
        second:
        phase= e^{ikR}. We use the first convention here.

        :param k: kpoint
        :param convention: 1 or 2. 
        """
        Hk = np.zeros((self.nbasis, self.nbasis), dtype='complex')
        if convention == 2:
            for R, mat in self.data.items():
                phase = np.exp(self.R2kfactor* np.dot(k, R))
                H = mat * phase
                Hk += H + H.conjugate().T
        elif convention == 1:
            for R, mat in self.data.items():
                phase = np.exp(self.R2kfactor* np.dot(k, R + self.rjminusri))
                H = mat * phase
                Hk += H + H.conjugate().T
        else:
            raise ValueError("convention should be either 1 or 2.")
        return Hk

    def solve(self, k, convention=2):
        Hk=self.gen_ham(k, convention=convention)
        return eigh(Hk)


    def HS_and_eigen(self, kpts, convention=2):
        """
        calculate eigens for all kpoints. 
        :param kpts: list of k points.
        """
        nk = len(kpts)
        hams = np.zeros((nk, self.nbasis, self.nbasis), dtype=complex)
        evals = np.zeros((nk, self.nbasis), dtype=float)
        evecs = np.zeros((nk, self.nbasis, self.nbasis),dtype=complex)
        for ik, k in enumerate(kpts):
            hams[ik]=self.gen_ham(tuple(k), convention=convention)
            evals[ik], evecs[ik] = eigh(hams[ik])
        return hams, None, evals, evecs


    def prepare_phase_rjri(self):
        """
        The matrix P: P(i, j) = r(j)-r(i)
        """
        self.rjminusri = np.zeros((self.nbasis, self.nbasis, self.ndim),
                                  dtype=float)
        for i in range(self.nbasis):
            for j in range(self.nbasis):
                self.rjminusri[i, j] = self.xred[j] - self.xred[i]

    def to_sparse(self):
        for key, val in self.data:
            self.data[key] = self._matrix(val)

    @property
    def Rlist(self):
        return list(self.data.keys())

    @property
    def nR(self):
        """
        number of R
        """
        return len(self.Rlist)

    @property
    def site_energies(self):
        """
        on site energies.
        """
        return self.data[(0, 0, 0)].diagonal() * 2

    @property
    def ham_R0(self):
        """
        return hamiltonian at R=0. Note that the data is halfed for R=0.
        """
        return self.data[(0, 0, 0)] + self.data[(0, 0, 0)].T.conj()

    def get_hamR(self, R):
        """
        return the hamiltonian at H(i, j) at R.
        """
        nzR = np.nonzero(R)[0]
        if len(nzR) != 0 and R[nzR[0]] < 0:
            newR = tuple(-np.array(R))
            return self.data[newR].T.conj()
        elif len(nzR) == 0:
            newR = R
            mat = self.data[newR]
            return mat + self.data[(0, 0, 0)].T.conj()
        else:
            newR = R
            return self.data[newR]

    @staticmethod
    def _positive_R_mat(R, mat):
        nzR = np.nonzero(R)[0]
        if len(nzR) != 0 and R[nzR[0]] < 0:
            newR = tuple(np.array(-R))
            newmat = mat.T.conj()
        elif len(nzR) == 0:
            newR = R
            newmat = (mat + mat.T.conj()) / 2.0
        else:
            newR = R
            newmat = mat
        return newR, newmat

    def _to_positive_R(self):
        """
        make all the R positive. 
        t(i, j, R) = t(j, i, -R).conj() if R is negative.
        """
        new_MyTB = MyTB(self.nbasis, sparse=self.sparse)
        for R, mat in self.data:
            newR, newmat = self._positive_R_mat(R, mat)
            new_MyTB[newR] += newmat
        return new_MyTB

    def shift_position(self, rpos):
        """
        shift the positions of basis set to near reference positions.
        E.g. reduced position 0.8, with refernce 0.0 will goto -0.2. 
        This can move the wannier functions to near the ions. 
        """
        pos = self.positions
        shift = np.zeros((self.nbasis, self.ndim), dtype='int')
        shift[:, :] = np.round(pos - rpos)
        newpos = copy.deepcopy(pos)
        for i in range(self.nbasis):
            newpos[i] = pos[i] - shift[i]
        d = MyTB(self.nbasis, ndim=self.ndim, nspin=self.nspin)
        d._positions = newpos

        for R, v in self.data.items():
            for i in range(self.nbasis):
                for j in range(self.nbasis):
                    sR = tuple(np.array(R) - shift[i] + shift[j])
                    nzR = np.nonzero(sR)[0]
                    if len(nzR) != 0 and sR[nzR[0]] < 0:
                        newR = tuple(-np.array(sR))
                        d.data[newR][j, i] += v[i, j].conj()
                    elif len(nzR) == 0:
                        newR = sR
                        d.data[newR][i, j] += v[i, j]*0.5
                        d.data[newR][j, i] += v[i, j].conj()*0.5
                    else:
                        d.data[sR][i, j] += v[i, j]
        return d

    def save(self, fname):
        """
        Save model into a netcdf file.
        :param fname: filename.
        """
        #from netCDF4 import Dataset
        # root = Dataset(fname, 'w', format="NETCDF4")
        root = netcdf_file(fname, mode='w')
        root.createDimension("nR", self.nR)
        root.createDimension("ndim", self.ndim)
        root.createDimension("nbasis", self.nbasis)
        root.createDimension("nspin", self.nspin)
        root.createDimension("natom", len(self.atoms))
        R = root.createVariable("R", 'i4', ("nR", "ndim"))
        data_real = root.createVariable("data_real", 'f8',
                                        ("nR", "nbasis", "nbasis"))
        data_imag = root.createVariable("data_imag", 'f8',
                                        ("nR", "nbasis", "nbasis"))
        positions = root.createVariable("positions", 'f8', ("nbasis", "ndim"))

        if self.atoms is not None:
            atom_numbers = root.createVariable("atom_numbers", 'i4',
                                               ("natom", ))
            atom_xred = root.createVariable("atom_xred", 'f8',
                                            ("natom", "ndim"))
            atom_cell = root.createVariable("atom_cell", 'f8',
                                            ("ndim", "ndim"))

        atom_cell.unit = 'Angstrom'
        positions.unit = "1"
        data_real.unit = 'eV'
        data_imag.unit = 'eV'

        R[:] = np.array(self.Rlist)
        d = np.array(tuple(self.data.values()))
        data_real[:] = np.real(d)
        data_imag[:] = np.imag(d)
        positions[:] = np.array(self.positions)

        if self.atoms is not None:
            atom_numbers[:] = np.array(self.atoms.get_atomic_numbers())
            atom_xred[:] = np.array(self.atoms.get_scaled_positions())
            atom_cell[:] = np.array(self.atoms.get_cell())
        root.close()

    @staticmethod
    def load_MyTB(fname):
        """
        Load from a netcdf file.
        :param fname: netcdf filename.
        :Returns: tight binding model.
        """
        #from netCDF4 import Dataset
        root = netcdf_file(fname, 'r', mmap=False)
        nbasis = root.dimensions['nbasis']
        nspin = root.dimensions['nspin']
        ndim = root.dimensions['ndim']
        natom = root.dimensions['natom']
        Rlist = root.variables['R'][:]
        mdata_real = root.variables['data_real'][:]
        mdata_imag = root.variables['data_imag'][:]
        positions = root.variables['positions'][:]

        atom_numbers = root.variables['atom_numbers'][:]
        atom_xred = root.variables['atom_xred'][:]
        atom_cell = root.variables['atom_cell'][:]
        atoms = Atoms(
            numbers=atom_numbers, scaled_positions=atom_xred, cell=atom_cell)
        m = MyTB(nbasis, nspin=nspin, ndim=ndim, positions=positions)
        m.atoms = copy.deepcopy(atoms)
        root.close()
        for iR, R in enumerate(Rlist):
            m.data[tuple(R)] = mdata_real[iR] + mdata_imag[iR] * 1j
        return m

    @staticmethod
    def from_tbmodel(model):
        """
        translate from a tbmodel type tight binding model
        """
        ret = MyTB(nbasis=model.size)
        for R, v in model.hop.items():
            ret.data[R] = v
        ret._positions = np.reshape(model.pos, (model.size, model.dim))
        return ret

    @staticmethod
    def from_tbmodel_hdf5(fname):
        """
        load model from a hdf5 file. It uses the tbmodel parser.
        """

        from tbmodels import Model
        m = Model.from_hdf5_file(fname)
        ret = MyTB(nbasis=m.size)
        for R, v in m.hop.items():
            ret.data[R] = v
        ret.positions = np.reshape(m.pos, (m.size, m.ndim))
        return ret

    def to_spin_polarized(self, order=1):
        """
        repeat to get spin polarized.
        order =1 : orb1_up, orb1_dn, orb2_up, orb2_dn...
        order =2 : orb1_up, orb2_up, ... orb1_dn, orb2_dn...
        """
        ret = MyTB(self.nbasis * 2)
        if self.positions is None:
            ret.positions = None
        else:
            ret.positions = np.repeat(self.positions, 2, axis=0)
        for R, mat in self.data.items():
            if order == 1:
                ret.data[R][::2, ::2] = mat
                ret.data[R][1::2, 1::2] = mat
            elif order == 2:
                ret.data[R][:self.norb, :self.norb] = mat
                ret.data[R][self.norb:, self.norb:] = mat
        return ret

    #def plot_band(self, kpts, color='green', show=False):
    #    evals, evecs = self.solve_all(kpts)
    #    for i in range(self.nbasis):
    #        plt.plot(evals[:, i], color=color)
    #    if show():
    #        plt.show()

    def validate(self):
        # make sure all R are 3d.
        for R in self.data.keys():
            if len(R) != self.ndim:
                raise ValueError(
                    "Dimension of R should be ndim %s" % (self.ndim))


def merge_tbmodels_spin(tbmodel_up, tbmodel_dn):
    """
    Merge a spin up and spin down model to one spinor model.
    """
    tbmodel = MyTB(
        nbasis=tbmodel_up.nbasis * 2,
        data=None,
        positions=np.vstack([tbmodel_up.positions, tbmodel_dn.positions]),
        sparse=False,
        ndim=tbmodel_up.ndim,
        nspin=2,
        double_site_energy=2.0)
    norb = tbmodel.norb
    for R in tbmodel_up.data:
        tbmodel.data[R][:norb, :norb] = tbmodel_up.data[R][:, :]
        tbmodel.data[R][norb:, norb:] = tbmodel_dn.data[R][:, :]
    return tbmodel
