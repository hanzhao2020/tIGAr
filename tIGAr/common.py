"""
The ``common`` module 
---------------------
contains basic definitions of abstractions for 
generating extraction data and importing it again for use in analysis.  Upon
importing it, a number of setup steps are carried out (e.g., initializing MPI).
"""

from dolfin import *
import petsc4py, sys
petsc4py.init(sys.argv)
from petsc4py import PETSc

import math
import numpy
import abc
from numpy import array
from scipy.stats import mode
from numpy import argsort
from numpy import zeros
from numpy import full
from numpy import transpose as npTranspose
from numpy import arange

from dolfin import MPI, mpi_comm_world

if(parameters.linear_algebra_backend != 'PETSc'):
    print("ERROR: tIGAr requires PETSc.")
    exit()
    
mycomm = mpi_comm_world()
mpisize = MPI.size(mycomm)
mpirank = MPI.rank(mycomm)

from tIGAr.calculusUtils import *

INDEX_TYPE = 'int32'
#DEFAULT_PREALLOC = 100
DEFAULT_PREALLOC = 500

# file naming conventions
EXTRACTION_DATA_FILE = "extraction-data.h5"
EXTRACTION_INFO_FILE = "extraction-info.txt"
EXTRACTION_H5_MESH_NAME = "/mesh"
def EXTRACTION_H5_CONTROL_FUNC_NAME(dim):
    return "/control"+str(dim)
#def EXTRACTION_ZERO_DOFS_FILE(proc):
#    return "/zero-dofs"+str(proc)+".dat"
EXTRACTION_ZERO_DOFS_FILE = "zero-dofs.dat"
EXTRACTION_MAT_FILE = "extraction-mat.dat"
EXTRACTION_MAT_FILE_CTRL = "extraction-mat-ctrl.dat"

# DG space is more memory-hungry, but allows for $C^{-1}$-continuous splines,
# e.g., for div-conforming VMS; maybe add mechanism later to choose this
# automatically, based on spline, w/ option for manual override.
#EXTRACTION_ELEMENT = "Lagrange"
USE_DG_DEFAULT = True

# whether or not to use tensor product elements by default
USE_RECT_ELEM_DEFAULT = True

# helper function to generate an identity permutation IS 
# given an ownership range
def generateIdentityPermutation(ownRange):

    """
    Returns a PETSc index set corresponding to the ownership range.
    """
    
    iStart = ownRange[0]
    iEnd = ownRange[1]
    localSize = iEnd - iStart
    iArray = zeros(localSize,dtype=INDEX_TYPE)
    for i in arange(0,localSize):
        iArray[i] = i+iStart
    retval = PETSc.IS()
    retval.createGeneral(iArray)
    return retval

class AbstractExtractionGenerator(object):

    """
    Abstract class representing the minimal set of functions needed to write
    extraction operators for a spline.
    """

    __metaclass__ = abc.ABCMeta

    def __init__(self,*args):

        """
        Arguments in ``*args`` are passed as a tuple to 
        ``self.customSetup()``.  Appropriate arguments vary by subclass.
        """
        
        self.customSetup(args)
        self.genericSetup()
        
    # what type of element (CG or DG) to extract to
    # (override in subclass for non-default behavior)
    def useDG(self):

        """
        Returns a Boolean, indicating whether or not to use DG elements 
        in extraction.
        """
        
        return USE_DG_DEFAULT
    
    def extractionElement(self):

        """
        Returns a string indicating what type of FE to use in extraction.
        """
        
        if(self.useDG()):
            return "DG"
        else:
            return "Lagrange"
        
    @abc.abstractmethod
    def customSetup(self,args):
        """
        Customized instructions to execute during initialization.  ``args``
        is a tuple of custom arguments.
        """
        return

    @abc.abstractmethod
    def getNFields(self):
        """
        Returns the number of unknown fields for the spline.
        """
        return

    @abc.abstractmethod
    def getHomogeneousCoordinate(self,node,direction):
        """
        Return the ``direction``-th homogeneous coordinate of the ``node``-th
        control point of the spline.
        """
        return

    @abc.abstractmethod
    def generateMesh(self):
        """
        Generate and return an FE mesh suitable for extracting the 
        subclass's spline space.
        """
        return

    @abc.abstractmethod
    def getDegree(self,field):
        """
        Return the degree of polynomial to be used in the extracted
        representation of a given ``field``, with ``-1`` being the 
        control field.
        """
        return

    @abc.abstractmethod
    def getNcp(self,field):
        """
        Return the total number of degrees of freedom of a given ``field``,
        with field ``-1`` being the control mesh field.
        """
        return

    @abc.abstractmethod
    def getNsd(self):
        """
        Return the number of spatial dimensions of the physical domain.
        """
        return

    def globalDof(self,field,localDof):
        """
        Given a ``field`` and a local DoF number ``localDof``, 
        return the global DoF number; 
        this is BEFORE any re-ordering for parallelization.
        """
        # offset localDof by 
        retval = localDof
        for i in range(0,field):
            retval += self.getNcp(i)
        return retval
    
    def generatePermutation(self):
        """
        Generates an index set to permute the IGA degrees of freedom
        into an order that is (hopefully) efficient given the partitioning
        of the FEM nodes.  Assume that ``self.M`` currently holds the
        un-permuted extraction matrix.
        Default implementation just fills in an identity permutation.
        """
        return generateIdentityPermutation\
            (self.M.mat().getOwnershipRangeColumn())
        
    def addZeroDofsGlobal(self,newDofs):
        """
        Adds new DoFs in the list ``newDofs`` in global numbering 
        to the list of DoFs to which
        homogeneous Dirichlet BCs will be applied during analysis.
        """
        self.zeroDofs += newDofs
        
    def addZeroDofs(self,field,newDofs):
        """
        Adds new DoFs in the list ``newDofs`` in local numbering for a 
        given ``field`` to the list of DoFs to which
        homogeneous Dirichlet BCs will be applied during analysis.
        """
        newDofsGlobal = newDofs[:]
        for i in range(0,len(newDofs)):
            newDofsGlobal[i] = self.globalDof(field,newDofs[i])
        self.addZeroDofsGlobal(newDofsGlobal)
    
    def getPrealloc(self,control):
        """
        Returns the number of entries per row needed in the extraction matrix.
        The parameter ``control`` is a Boolean indicating whether or not this 
        is the preallocation for the scalar field used for control point 
        coordinates.

        If left as the default, this could potentially slow down drastically
        for very high-order splines, or waste a lot of memory for low order
        splines.  In general, it is a good idea to override this in 
        subclasses.
        """
        return DEFAULT_PREALLOC
    
    @abc.abstractmethod
    def generateM_control(self):
        """
        Return the extraction matrix for the control field.
        """
        return

    @abc.abstractmethod
    def generateM(self):
        """
        Return the extraction matrix for the unknowns.
        """
        return
        
    def genericSetup(self):
        """
        Common setup steps for all subclasses (called in ``self.__init__()``).
        """
        
        self.mesh = self.generateMesh()

        # note: if self.nsd is set in a customSetup, then the subclass
        # getNsd() references that, this is still safe
        self.nsd = self.getNsd()

        self.VE_control = FiniteElement(self.extractionElement(),\
                                        self.mesh.ufl_cell(),\
                                        self.getDegree(-1))
        self.V_control = FunctionSpace(self.mesh,self.VE_control)

        if(self.getNFields() > 1):
            VE_components = []
            for i in range(0,self.getNFields()):
                VE_components \
                    += [FiniteElement(self.extractionElement(),\
                                      self.mesh.ufl_cell(),\
                                      self.getDegree(i)),]

            self.VE = MixedElement(VE_components)
        else:
            self.VE = FiniteElement(self.extractionElement(),\
                                    self.mesh.ufl_cell(),\
                                    self.getDegree(0))
            
        self.V = FunctionSpace(self.mesh,self.VE)

        self.cpFuncs = []
        for i in range(0,self.nsd+1):
            self.cpFuncs += [Function(self.V_control),]

        self.M_control = self.generateM_control()
        self.M = self.generateM()        

        # may need to be permuted
        self.zeroDofs = [] #self.generateZeroDofs()
        
        # replace M with permuted version
        #if(mpisize > 1):
        #
        #    self.permutation = self.generatePermutation()
        #    newM = self.M.mat()\
        #                 .permute\
        #                 (generateIdentityPermutation\
        #                  (self.M.mat().getOwnershipRange()),\
        #                  self.permutation)
        #    self.M = PETScMatrix(newM)
        #
        #    # fix list of zero DOFs
        #    self.permutationAO = PETSc.AO()
        #    self.permutationAO\
        #        .createBasic(self.permutation,\
        #                     generateIdentityPermutation\
        #                     (self.M.mat().getOwnershipRangeColumn()))
        #    zeroDofIS = PETSc.IS()
        #    zeroDofIS.createGeneral(array(self.zeroDofs,dtype=INDEX_TYPE))
        #    self.zeroDofs = self.permutationAO.app2petsc\
        #                    (zeroDofIS).getIndices()
            
    def applyPermutation(self):
        """
        Permutes the order of the IGA degrees of freedom, so that their
        parallel partitioning better aligns with that of the FE degrees 
        of freedom, which is generated by standard mesh-partitioning
        approaches in FEniCS.  
        """
        if(mpisize > 1):

            self.permutation = self.generatePermutation()
            newM = self.M.mat()\
                         .permute\
                         (generateIdentityPermutation\
                          (self.M.mat().getOwnershipRange()),\
                          self.permutation)
            self.M = PETScMatrix(newM)

            # fix list of zero DOFs
            self.permutationAO = PETSc.AO()
            self.permutationAO\
                .createBasic(self.permutation,\
                             generateIdentityPermutation\
                             (self.M.mat().getOwnershipRangeColumn()))
            zeroDofIS = PETSc.IS()
            zeroDofIS.createGeneral(array(self.zeroDofs,dtype=INDEX_TYPE))
            self.zeroDofs = self.permutationAO.app2petsc\
                            (zeroDofIS).getIndices()
    
    def writeExtraction(self,dirname,doPermutation=True):
        """
        Writes all extraction data to files in a directory named 
        ``dirname``.  The optional argument ``doPermutation`` is a Boolean
        indicating whether or not to permute the unknowns for better
        parallel performance in matrix--matrix multiplications.  (Computing
        this permuation may be slow for large meshes.)
        """
        # need:
        # - HDF5 file w/
        # -- mesh
        # -- extracted CPs, weights
        # - Serialized PETSc matrix for M_control
        # - Serialized PETSc matrix for M
        # - txt file w/
        # -- nsd
        # -- number of fields
        # -- for each field (+ scalar control field)
        # --- function space info (element type, degree)
        # - File for each processor listing zero-ed dofs

        if(doPermutation):
            self.applyPermutation()
        
        # get transpose
        MT_control = PETScMatrix(self.M_control.mat().transpose(PETSc.Mat()))
        #MT = PETScMatrix(self.M.mat().transpose(PETSc.Mat()))

        # generating CPs, weights in spline space:
        # (control net never permuted)
        for i in range(0,self.nsd+1):
            MTC = MT_control*(self.cpFuncs[i].vector())
            Istart, Iend = as_backend_type(MTC).vec().getOwnershipRange()
            for I in arange(Istart, Iend):
                as_backend_type(MTC).vec()[I] \
                    = self.getHomogeneousCoordinate(I,i)
            as_backend_type(MTC).vec().assemblyBegin()
            as_backend_type(MTC).vec().assemblyEnd()

            self.cpFuncs[i].vector().set_local((self.M_control*MTC).get_local())
            as_backend_type(self.cpFuncs[i].vector()).vec().ghostUpdate()

        # write HDF file
        f = HDF5File(mpi_comm_world(),dirname+"/"+EXTRACTION_DATA_FILE,"w")
        f.write(self.mesh,EXTRACTION_H5_MESH_NAME)

        for i in range(0,self.nsd+1):
            f.write(self.cpFuncs[i],EXTRACTION_H5_CONTROL_FUNC_NAME(i))
        f.close()

        # PETSc matrices
        viewer = PETSc.Viewer().createBinary(dirname+"/"\
                                             +EXTRACTION_MAT_FILE, 'w')
        viewer(self.M.mat())
        viewer = PETSc.Viewer().createBinary(dirname+"/"\
                                             +EXTRACTION_MAT_FILE_CTRL, 'w')
        viewer(self.M_control.mat())

        # write out zero-ed dofs 
        #dofList = self.zeroDofs
        #fs = ""
        #for dof in dofList:
        #    fs += str(dof)+" "
        #f = open(dirname+"/"+EXTRACTION_ZERO_DOFS_FILE,"w")
        #f.write(fs)
        #f.close()
        zeroDofIS = PETSc.IS()
        zeroDofIS.createGeneral(array(self.zeroDofs,dtype=INDEX_TYPE))
        viewer = PETSc.Viewer().createBinary(dirname+"/"\
                                             +EXTRACTION_ZERO_DOFS_FILE, 'w')
        viewer(zeroDofIS)
        
        # write info
        if(mpirank == 0):
            fs = str(self.nsd)+"\n"\
                 + self.extractionElement()+"\n"\
                 + str(self.getNFields())+"\n"
            for i in range(-1,self.getNFields()):
                fs += str(self.getDegree(i))+"\n"\
                      + str(self.getNcp(i))+"\n"
            f = open(dirname+"/"+EXTRACTION_INFO_FILE,'w')
            f.write(fs)
            f.close()
        MPI.barrier(mycomm)

class SplineDisplacementExpression(Expression):

    """
    An expression that can be used to evaluate ``F`` plus an optional 
    displacement at arbitrary points.  To be usable, it must have the 
    following attributes assigned: 

    (1) ``self.spline``: an instance of ``ExtractedSpline`` to which the 
    displacement applies. 

    (2) ``self.functionList:`` a list of scalar functions in the 
    function space for ``spline``'s control mesh, which act as components of 
    the displacement. If ``functionList`` contains too few entries (including 
    zero entries), the missing entries are assumed to be zero.
    """
    
    # needs attributes:
    # - spline (ExtractedSpline)
    # - functionList (list of SCALAR Functions)
    
    def eval_cell(self,values,x,c):
        phi = []
        out = array([0.0,])
        for i in range(0,self.spline.nsd):
            self.spline.cpFuncs[i].set_allow_extrapolation(True)
            #phi += [self.cpFuncs[i](Point(x)),]
            self.spline.cpFuncs[i].eval_cell(out,x,c)
            phi += [out[0],]
        self.spline.cpFuncs[self.spline.nsd].set_allow_extrapolation(True)
        for i in range(0,self.spline.nsd):
            if(i<len(self.functionList)):
                self.functionList[i].set_allow_extrapolation(True)
                self.functionList[i].eval_cell(out,x,c)
                phi[i] += out[0]
        #w = self.cpFuncs[self.nsd](Point(x))
        self.spline.cpFuncs[self.spline.nsd].eval_cell(out,x,c)
        w = out[0]
        for i in range(0,self.spline.nsd):
            phi[i] = phi[i]/w
        xx = []
        for i in range(0,self.spline.nsd):
            if(i<len(x)):
                xx += [x[i],]
            else:
                xx += [0,]
        for i in range(0,self.spline.nsd):
            values[i] = phi[i] - xx[i]
            
    #def value_shape(self):
    #    return (self.spline.nsd,)
    

# compose with deformation
class tIGArExpression(Expression):

    """
    A subclass of ``Expression`` which composes its attribute ``self.expr``
    (also an ``Expression``) with the deformation ``F`` given by its attribute 
    ``self.cpFuncs``, which is a list of ``Function`` objects, specifying the 
    components of ``F``.
    """

    # using eval_cell allows us to avoid having to search for which cell
    # x is in; also x need not be in a unique cell, which is nice for
    # splines that do not have a single coordinate chart
    def eval_cell(self,values,x,c):
        phi = []
        out = array([0.0,])
        for i in range(0,self.nsd):
            self.cpFuncs[i].set_allow_extrapolation(True)
            self.cpFuncs[i].eval_cell(out,x,c)
            phi += [out[0],]
        self.cpFuncs[self.nsd].set_allow_extrapolation(True)
        self.cpFuncs[self.nsd].eval_cell(out,x,c)
        w = out[0]
        for i in range(0,self.nsd):
            phi[i] = phi[i]/w
        self.expr.eval(values,array(phi))

# could represent any sort of spline that is extractable
class ExtractedSpline(object):

    """
    A class representing an extracted spline.  The idea is that all splines
    look the same after extraction, so there is no need for a proliferation
    of different classes to cover NURBS, T-splines, etc. (as there is for
    extraction generators).  
    """

    def __init__(self,sourceArg,quadDeg,mesh=None,doPermutation=True):

        """
        Generates instance from extraction data in ``sourceArg``, which
        might either be an ``AbstractExtractionGenerator`` or the name of
        a directory containing extraction data.
        Optionally takes a ``mesh`` argument, so that function spaces can be
        established on the same mesh as an existing spline object for
        facilitating segregated solver schemes.  (Splines common to one
        set of extraction data are always treated as a monolothic mixed
        function space.)  Everything to do with the spline is integrated 
        using a quadrature rule of degree ``quadDeg``.
        The argument ``doPermutation`` chooses whether or not to apply a
        permutation to the IGA DoF order.  It is ignored if reading
        extraction data from the filesystem.
        """

        if(isinstance(sourceArg,AbstractExtractionGenerator)):
            self.initFromGenerator(sourceArg,quadDeg,mesh)
        else:
            self.initFromFilesystem(sourceArg,quadDeg,mesh)
            
        self.genericSetup()
            

    def initFromGenerator(self,generator,quadDeg,mesh=None,
                          doPermutation=True):
        """
        Generates instance from an ``AbstractExtractionGenerator``, without
        passing through the filesystem.  This mainly exists to circumvent
        broken parallel HDF5 file output for quads and hexes in 2017.2 
        (See Issue #1000 for dolfin on Bitbucket.)  
        
        NOTE: While seemingly-convenient for small-scale testing,
        this is not the preferred workflow for most realistic 
        cases, as it forces a possibly-expensive preprocessing step to 
        execute every time the analysis code is run.  
        """

        if(doPermutation):
            generator.applyPermutation()
        
        self.quadDeg = quadDeg
        self.nsd = generator.getNsd()
        self.elementType = generator.extractionElement()
        self.nFields = generator.getNFields()
        self.p_control = generator.getDegree(-1)
        self.p = []
        for i in range(0,self.nFields):
            self.p += [generator.getDegree(i)]
        if(mesh==None):
            self.mesh = generator.mesh
        else:
            self.mesh = mesh
        self.cpFuncs = generator.cpFuncs
        self.VE = generator.VE
        self.VE_control = generator.VE_control
        self.V = generator.V
        self.V_control = generator.V_control
        self.M = generator.M
        self.M_control = generator.M_control
        zeroDofIS = PETSc.IS()
        zeroDofIS.createGeneral(array(generator.zeroDofs,dtype=INDEX_TYPE))
        self.zeroDofs = zeroDofIS
            
    def initFromFilesystem(self,dirname,quadDeg,mesh=None):

        """
        Generates instance from extraction data in directory ``dirname``. 
        Optionally takes a ``mesh`` argument, so that function spaces can be
        established on the same mesh as an existing spline object for
        facilitating segregated solver schemes.  (Splines common to one
        set of extraction data are always treated as a monolothic mixed
        function space.)  Everything to do with the spline is integrated 
        using a quadrature rule of degree ``quadDeg``.
        """

        self.quadDeg = quadDeg

        # read function space info
        f = open(dirname+"/"+EXTRACTION_INFO_FILE,'r')
        fs = f.read()
        f.close()
        lines = fs.split('\n')
        lineCount = 0
        self.nsd = int(lines[lineCount])
        lineCount += 1
        self.elementType = lines[lineCount]
        lineCount += 1
        self.nFields = int(lines[lineCount])
        lineCount += 1
        self.p_control = int(lines[lineCount])
        lineCount += 1
        ncp_control = int(lines[lineCount])
        lineCount += 1
        self.p = []
        ncp = []
        for i in range(0,self.nFields):
            self.p += [int(lines[lineCount]),]
            lineCount += 1
            ncp += [int(lines[lineCount]),]
            lineCount += 1
        #prealloc_control = int(lines[lineCount])
        #lineCount += 1
        #prealloc = int(lines[lineCount])

        # read mesh if none provided
        f = HDF5File(mpi_comm_world(),dirname+"/"+EXTRACTION_DATA_FILE,'r')
        if(mesh==None):
            self.mesh = Mesh()

            # NOTE: behaves erratically in parallel for quad/hex meshes
            # in 2017.2; hopefully will be fixed soon (see dolfin
            # issue #1000).  
            f.read(self.mesh,EXTRACTION_H5_MESH_NAME,True)

        else:
            self.mesh = mesh
        
        # create function spaces
        self.VE_control\
            = FiniteElement(self.elementType,self.mesh.ufl_cell(),\
                            self.p_control)
        self.V_control\
            = FunctionSpace(self.mesh,self.VE_control)

        if(self.nFields > 1):
            VE_components = []
            for i in range(0,self.nFields):
                VE_components \
                    += [FiniteElement(self.elementType,self.mesh.ufl_cell(),\
                                      self.p[i]),]
            self.VE = MixedElement(VE_components)
        else:
            self.VE = FiniteElement(self.elementType,self.mesh.ufl_cell(),\
                                    self.p[0])
            
        self.V = FunctionSpace(self.mesh,self.VE)
        
        # read control functions
        self.cpFuncs = []
        for i in range(0,self.nsd+1):
            self.cpFuncs += [Function(self.V_control),]
            f.read(self.cpFuncs[i],\
                   EXTRACTION_H5_CONTROL_FUNC_NAME(i))
        f.close()
        
        # read extraction matrix and create transpose for control space
        Istart, Iend = as_backend_type\
                       (self.cpFuncs[0].vector()).vec().getOwnershipRange()
        nLocalNodes = Iend - Istart
        MPETSc = PETSc.Mat()
        MPETSc.create(PETSc.COMM_WORLD)
        # arguments: [[localRows,localColumns],[globalRows,globalColums]]
        # or is it [[localRows,globalRows],[localColumns,globalColums]]?
        # the latter seems to be what comes out of getSizes()...
        if(mpisize > 1):
            MPETSc.setSizes([[nLocalNodes,None],[None,ncp_control]])
        #MPETSc.setType('aij') # sparse
        #MPETSc.setPreallocationNNZ(prealloc_control)
        #MPETSc.setUp()
        viewer \
            = PETSc.Viewer().createBinary(dirname\
                                          +"/"+EXTRACTION_MAT_FILE_CTRL,\
                                          'r')
        
        self.M_control = PETScMatrix(MPETSc.load(viewer))

        #exit()
        
        # read extraction matrix and create transpose
        Istart, Iend = as_backend_type\
                       (Function(self.V).vector()).vec().getOwnershipRange()
        nLocalNodes = Iend - Istart
        totalDofs = 0
        for i in range(0,self.nFields):
            totalDofs += ncp[i]
        MPETSc2 = PETSc.Mat()
        MPETSc2.create(PETSc.COMM_WORLD)
        # arguments: [[localRows,localColumns],[globalRows,globalColums]]
        if(mpisize > 1):
            MPETSc2.setSizes([[nLocalNodes,None],[None,totalDofs]])
        #MPETSc2.setType('aij') # sparse
        #MPETSc2.setPreallocationNNZ(prealloc)
        #MPETSc2.setUp()
        viewer \
            = PETSc.Viewer().createBinary(dirname\
                                          +"/"+EXTRACTION_MAT_FILE,'r')
        self.M = PETScMatrix(MPETSc2.load(viewer))

        # read zero-ed dofs
        #f = open(dirname+"/"+EXTRACTION_ZERO_DOFS_FILE(mpirank),"r")
        #f = open(dirname+"/"+EXTRACTION_ZERO_DOFS_FILE,"r")
        #fs = f.read()
        #f.close()
        #dofStrs = fs.split()
        #zeroDofs  = []
        #for dofStr in dofStrs:
        #    # only keep the ones for this processor
        #    possibleDof = int(dofStr)
        #    if(possibleDof < Iend and possibleDof >= Istart):
        #        zeroDofs += [possibleDof,]
        #self.zeroDofs = PETSc.IS()
        #self.zeroDofs.createGeneral(array(zeroDofs,dtype=INDEX_TYPE))

        viewer = PETSc.Viewer()\
                      .createBinary\
                      (dirname+"/"+EXTRACTION_ZERO_DOFS_FILE,"r")
        self.zeroDofs = PETSc.IS()
        self.zeroDofs.load(viewer)


    def genericSetup(self):

        """
        Setup steps to take regardless of the source of extraction data.
        """
        
        # for marking subdomains
        #self.boundaryMarkers = FacetFunctionSizet(self.mesh,0)
        self.boundaryMarkers \
            = MeshFunctionSizet(self.mesh,self.mesh.topology().dim()-1,0)
        
        # caching transposes of extraction matrices
        self.MT_control \
            = PETScMatrix(self.M_control.mat().transpose(PETSc.Mat()))
        self.MT \
            = PETScMatrix(self.M.mat().transpose(PETSc.Mat()))
        
        # geometrical mapping
        components = []
        for i in range(0,self.nsd):
            components += [self.cpFuncs[i]/self.cpFuncs[self.nsd],]
        self.F = as_vector(components)
        self.DF = grad(self.F)

        # debug
        #self.DF = Identity(self.nsd)

        # metric tensor
        self.g = getMetric(self.F) #(self.DF.T)*self.DF

        # normal of pre-image in coordinate chart
        self.N = FacetNormal(self.mesh)

        # normal that preserves orthogonality w/ pushed-forward tangent vectors
        self.n = mappedNormal(self.N,self.F)
        
        # integration measures
        self.dx = tIGArMeasure(volumeJacobian(self.g),dx,self.quadDeg)
        self.ds = tIGArMeasure(surfaceJacobian(self.g,self.N),\
                               ds,self.quadDeg,self.boundaryMarkers)

        # useful for defining Cartesian differential operators
        self.pinvDF = pinvD(self.F)

        # useful for tensors given in parametric coordinates
        self.gamma = getChristoffel(self.g)

        self.setSolverOptions()

        # linear space on mesh for projecting scalar fields onto
        self.VE_linear = FiniteElement("Lagrange",\
                                       self.mesh.ufl_cell(),1)
        #linearList = []
        #for i in range(0,self.nsd):
        #    linearList += [self.VE_linear,]
        #self.VE_displacement = MixedElement(linearList)
        
        self.VE_displacement = VectorElement\
                               ("Lagrange",self.mesh.ufl_cell(),1,\
                                dim=self.nsd)
        
        #self.VE_displacement = VectorElement\
        #                       ("Lagrange",self.mesh.ufl_cell(),1)
        
        self.V_displacement = FunctionSpace(self.mesh,self.VE_displacement)
        self.V_linear = FunctionSpace(self.mesh,self.VE_linear)

        
    def interpolateAsDisplacement(self,functionList=[]):

        """
        Given a list of scalar functions, get a displacement field from 
        mesh coordinates to control + functions in physical space, 
        interpolated on linear elements for plotting without discontinuities 
        on cut-up meshes. Default argument of ``functionList=[]`` 
        just interpolates the control functions.  If there are fewer elements
        in ``functionList`` than there are control functions, then the missing
        functions are assumed to be zero.

        NOTE: Currently only works with extraction to simplicial elements.
        """
        
        #expr = SplineDisplacementExpression(degree=self.quadDeg)
        expr = SplineDisplacementExpression\
               (element=self.VE_displacement)
        expr.spline = self
        expr.functionList = functionList
        disp = Function(self.V_displacement)
        disp.interpolate(expr)
        return disp
        
    # Cartesian differential operators in deformed configuration
    # N.b. that, when applied to tensor-valued f, f is considered to be
    # in the Cartesian coordinates of the physical configuration, NOT in the
    # local coordinate chart w.r.t. which derivatives are taken by FEniCS
    def grad(self,f,F=None):
        """ 
        Cartesian gradient of ``f`` w.r.t. physical coordinates.  
        Optional argument ``F`` can be used to take the gradient assuming 
        a different mapping from
        parametric to physical space.  (Default is ``self.F``.)
        """
        if(F==None):
            F = self.F
        return cartesianGrad(f,F)
    def div(self,f,F=None):
        """ 
        Cartesian divergence of ``f`` w.r.t. physical coordinates.  
        Optional argument ``F``
        can be used to take the gradient assuming a different mapping from
        parametric to physical space.  (Default is ``self.F``.)
        """
        if(F==None):
            F = self.F
        return cartesianDiv(f,F)
    # only applies in 3D, to vector-valued f
    def curl(self,f,F=None):
        """ 
        Cartesian curl w.r.t. physical coordinates.  Only applies in 3D, to
        vector-valued ``f``.  Optional argument ``F``
        can be used to take the gradient assuming a different mapping from
        parametric to physical space.  (Default is ``self.F``.)
        """
        if(F==None):
            F = self.F
        return cartesianCurl(f,F)
    
    # partial derivatives with respect to curvilinear coordinates; this is
    # just a wrapper for FEniCS grad(), but included to allow for writing
    # clear, unambiguous scripts
    def parametricGrad(self,f):
        """
        Gradient of ``f`` w.r.t. parametric coordinates.  (Equivalent to UFL 
        ``grad()``, but introduced to avoid confusion with ``self.grad()``.)
        """
        return grad(f)
    
    # curvilinear variants; if f is only a regular tensor, will create a
    # CurvilinearTensor w/ all indices lowered.  Metric defaults to one
    # generated by mapping self.F (into Cartesian space) if no metric is
    # supplied via f.
    def GRAD(self,f):
        """
        Covariant derivative of a ``CurvilinearTensor``, ``f``, taken w.r.t. 
        parametric coordinates, assuming that components
        of ``f`` are also given in this coordinate system.  If a regular tensor
        is passed for ``f``, a ``CurvilinearTensor`` will be created with all 
        lowered indices.
        """
        if(not isinstance(f,CurvilinearTensor)):
            ff = CurvilinearTensor(f,self.g)
        else:
            ff = f
        return curvilinearGrad(ff)
    def DIV(self,f):
        """
        Curvilinear divergence operator corresponding to ``self.GRAD()``. 
        Contracts new lowered index from ``GRAD`` with last raised 
        index of ``f``.
        If a regular tensor is passed for ``f``, a ``CurvilinearTensor``
        will be created with all raised indices.
        """
        if(not isinstance(f,CurvilinearTensor)):
            ff = CurvilinearTensor(f,self.g).sharp()
        else:
            ff = f
        return curvilinearDiv(ff)
    
    def spatialExpression(self,expr):
        """
        Converts string ``expr`` into an ``Expression``, 
        treating the coordinates ``'x[i]'`` in ``expr`` as 
        spatial coordinates.  
        (Using the standard ``Expression`` constructor, these would be treated 
        as parametric coordinates.)

        NOTE: Only works when extracting to simplicial elements.
        """
        retval = tIGArExpression(degree=self.quadDeg)
        retval.expr = Expression(expr,degree=self.quadDeg)
        retval.nsd = self.nsd
        retval.cpFuncs = self.cpFuncs
        return retval

    def parametricExpression(self,expr):
        """
        Create an ``Expression`` from a string, ``expr``, interpreting the
        coordinates ``'x[i]'`` in ``expr`` as parametric coordinates.
        Uses quadrature degree of spline object for interpolation degree.
        """
        return Expression(expr,degree=self.quadDeg)

    def parametricCoordinates(self):
        """
        Wrapper for ``SpatialCoordiantes()`` to avoid confusion, since
        FEniCS's spatial coordinates are used in tIGAr as parametric 
        coordinates.  
        """
        return SpatialCoordinate(self.mesh)

    def spatialCoordinates(self):
        """
        Returns the mapping ``self.F``, which gives the spatial coordinates
        of a parametric point.
        """
        return self.F
    
    def rationalize(self,u):
        """
        Divides its argument ``u`` by the weighting function of the spline's
        control mesh.
        """
        return u/(self.cpFuncs[self.nsd])

    def assembleLinearSystem(self,lhsForm,rhsForm,applyBCs=True):
        """
        Assembles a linear system corresponding the LHS form ``lhsForm`` and
        RHS form ``rhsForm``.  The optional argument ``applyBCs`` is a 
        Boolean indicating whether or not to apply the spline's 
        homogeneous Dirichlet BCs.
        """
        
        A = PETScMatrix()
        b = PETScVector()

        assemble(lhsForm, tensor=A)
        assemble(rhsForm, tensor=b)

        #import time
        #t0 = time.time()
        
        Am = as_backend_type(A).mat()
        MTm = as_backend_type(self.MT).mat()
        MTAm = MTm.matMult(Am)
        Mm = as_backend_type(self.M).mat()
        MTAMm = MTAm.matMult(Mm)
        MTAM = PETScMatrix(MTAMm)

        # MT determines parallel partitioning of MTb
        MTb = (self.MT)*b
        #U = u.vector()

        #t1 = time.time()

        #if(mpirank == 0):
        #    print("Time = ",t1-t0)
        
        #print MTAM.array()
        #exit()

        # apply zero bcs to MTAM and MTb
        # (default behavior is to set diag=1, as desired)
        as_backend_type(MTAM).mat().zeroRowsColumns(self.zeroDofs)
        as_backend_type(MTb).vec().setValues\
            (self.zeroDofs,zeros(self.zeroDofs.getLocalSize()))
        as_backend_type(MTAM).mat().assemblyBegin()
        as_backend_type(MTAM).mat().assemblyEnd()
        as_backend_type(MTb).vec().assemblyBegin()
        as_backend_type(MTb).vec().assemblyEnd()

        return (MTAM,MTb)

    def solveLinearSystem(self,MTAM,MTb,u):
        """
        Solves a linear system of the form

        ``MTAM*MTU = MTb``

        where ``MTAM`` is the IGA LHS, ``MTU`` is the vector of IGA unknowns
        (in the homogeneous coordinate representation, if rational splines
        are being used), and ``MTb`` is the IGA RHS.  The FE representation 
        of the solution is then the ``Function`` ``u`` which has a vector 
        of coefficients given by ``MT*MTU``.
        """
        
        U = u.vector()
        MTU = (self.MT)*U
        if(self.linearSolver == None):
            solve(MTAM,MTU,MTb)
        else:
            self.linearSolver.solve(MTAM,MTU,MTb)
        u.vector().set_local(((self.M)*MTU).get_local())
        as_backend_type(u.vector()).vec().ghostUpdate()
        as_backend_type(u.vector()).vec().assemble()

    
    def solveLinearVariationalProblem(self,residualForm,u,applyBCs=True):
        """
        Solves a linear variational problem with residual ``residualForm'',
        putting the solution in the ``Function`` ``u``.  Homogeneous 
        Dirichlet BCs from ``self`` can be optionally applied, based on the
        Boolean parameter ``applyBCs``.
        """
        lhsForm = lhs(residualForm)
        rhsForm = rhs(residualForm)

        if(rhsForm.integrals() == ()):
            v = TestFunction(self.V)
            rhsForm = Constant(0.0)*v[0]*self.dx
        
        MTAM,MTb = self.assembleLinearSystem(lhsForm,rhsForm,applyBCs)
        self.solveLinearSystem(MTAM,MTb,u)
        
        #return self.rationalize(u)

    def setSolverOptions(self,\
                         maxIters=20,\
                         relativeTolerance=1e-5,\
                         linearSolver=None):
        """
        Sets some solver options for the ``ExtractedSpline`` instance, to be
        used in ``self.solve*VariationalProblem()``.
        """
        self.maxIters = maxIters
        self.relativeTolerance = relativeTolerance
        self.linearSolver = linearSolver

    # couldn't figure out how to get subclassing NonlinearProblem to work...
    def solveNonlinearVariationalProblem(self,residualForm,J,u):
        """
        Solves a nonlinear variational problem with residual given by 
        ``residualForm``.  ``J`` is the functional derivative of 
        the residual w.r.t. the solution, ``u``, or some user-defined
        approximation thereof.
        """
        converged = False
        for i in range(0,self.maxIters):
            MTAM,MTb = self.assembleLinearSystem(J,residualForm)
            currentNorm = norm(MTb)
            if(i==0):
                initialNorm = currentNorm
            relativeNorm = currentNorm/initialNorm
            if(mpirank == 0):
                print("Solver iteration: "+str(i)+" , Relative norm: "\
                      + str(relativeNorm))
            if(currentNorm/initialNorm < self.relativeTolerance):
                converged = True
                break
            du = Function(self.V)
            #du.assign(Constant(0.0)*du)
            self.solveLinearSystem(MTAM,MTb,du)
            #as_backend_type(u.vector()).vec().assemble()
            #as_backend_type(du.vector()).vec().assemble()
            u.assign(u-du)
        if(not converged):
            print("ERROR: Nonlinear solver failed to converge.")
            exit()

    # project a scalar onto linears for plotting
    def projectScalarOntoLinears(self,toProject):
        """
        L2 projection of some UFL object ``toProject`` onto a space of linear,
        scalar FE functions (typically used for plotting).
        """
        u = TrialFunction(self.V_linear)
        v = TestFunction(self.V_linear)
        # don't bother w/ change of variables in integral
        res = inner(u-toProject,v)*self.dx.meas
        lhsForm = lhs(res)
        rhsForm = rhs(res)
        A = assemble(lhsForm)
        b = assemble(rhsForm)
        u = Function(self.V_linear)
        solve(A,u.vector(),b)
        return u
            
    # project something onto the solution space; ignore bcs by default
    def project(self,toProject,applyBCs=False):
        """
        L2 projection of some UFL object ``toProject`` onto the 
        ``ExtractedSpline`` object's solution space.  Can optionally apply
        homogeneous Dirichlet BCs with the Boolean parameter 
        ``applyBCs``.  By default, no BCs are applied in projection.
        """
        u = TrialFunction(self.V)
        v = TestFunction(self.V)
        u = self.rationalize(u)
        v = self.rationalize(v)
        res = inner(u-toProject,v)*self.dx
        retval = Function(self.V)
        self.solveLinearVariationalProblem(res,retval,applyBCs)
        retval = self.rationalize(retval)
        return retval
        
class AbstractCoordinateChartSpline(AbstractExtractionGenerator):

    """
    This abstraction epresents a spline whose parametric 
    coordinate system consists of a 
    using a single coordinate chart, so coordinates provide a unique set 
    of basis functions; this applies to single-patch B-splines, T-splines, 
    NURBS, etc., and, with a little creativity, can be stretched to cover
    multi-patch constructions.
    """
    
    @abc.abstractmethod
    def getNodesAndEvals(self,x,field):
        """
        Given a parametric point ``x``, return a list of the form
        
        ``[[index0, N_index0(x)], [index1,N_index1(x)], ... ]``
        
        where ``N_i`` is the ``i``-th basis function of the scalar polynomial 
        spline space (NOT of the rational space) corresponding to a given
        ``field``.
        """
        return

    # return a matrix M for extraction
    def generateM_control(self):
        """
        Generates the extraction matrix for the single scalar spline space
        used to represent all homogeneous components of the mapping ``F``
        from parametric to physical space.
        """
        
        func = Function(self.V_control)
        Istart, Iend = as_backend_type(func.vector()).vec().getOwnershipRange()
        nLocalNodes = Iend - Istart
        x_nodes = self.V_control.tabulate_dof_coordinates()\
                            .reshape((-1,self.mesh.geometry().dim()))

        MPETSc = PETSc.Mat()
        #MPETSc.create(PETSc.COMM_WORLD)
        # arguments: [[localRows,localColumns],[globalRows,globalColums]]
        #MPETSc.setSizes([[nLocalNodes,None],[None,self.getNcp(-1)]])
        #MPETSc.setType('aij') # sparse

        #MPETSc.create()
        
        MPETSc.createAIJ([[nLocalNodes,None],[None,self.getNcp(-1)]])
        MPETSc.setPreallocationNNZ([self.getPrealloc(True),
                                    self.getPrealloc(True)])

        # just slow down quietly if preallocation is insufficient
        MPETSc.setOption(PETSc.Mat.Option.NEW_NONZERO_ALLOCATION_ERR, False)
        # for debug:
        #MPETSc.setOption(PETSc.Mat.Option.NEW_NONZERO_ALLOCATION_ERR, True)
        MPETSc.setUp()
        
        # I indexes FEM nodes owned by this process
        #for I in xrange(Istart, Iend):
        dofs = self.V_control.dofmap().dofs()
        for I in arange(0,len(dofs)):
            x = x_nodes[dofs[I]-Istart]
            matRow = dofs[I]
            nodesAndEvals = self.getNodesAndEvals(x,-1)

            #cols = array(nodesAndEvals,dtype=INDEX_TYPE)[:,0]
            #rows = array([matRow,],dtype=INDEX_TYPE)
            #values = npTranspose(array(nodesAndEvals)[:,1:2])
            #MPETSc.setValues(rows,cols,values,addv=PETSc.InsertMode.INSERT)
            
            for i in range(0,len(nodesAndEvals)):
                MPETSc[matRow,nodesAndEvals[i][0]] = nodesAndEvals[i][1]

        MPETSc.assemblyBegin()
        MPETSc.assemblyEnd()
        
        return PETScMatrix(MPETSc)

    def generateM(self):
        """
        Generates the extraction matrix for the mixed function space of
        all unkown scalar fields.
        """
        
        func = Function(self.V)
        Istart, Iend = as_backend_type(func.vector()).vec().getOwnershipRange()
        nLocalNodes = Iend - Istart

        totalDofs = 0
        for i in range(0,self.getNFields()):
            totalDofs += self.getNcp(i)
        
        MPETSc = PETSc.Mat()
        #MPETSc.create(PETSc.COMM_WORLD)
        # arguments: [[localRows,localColumns],[globalRows,globalColums]]
        #MPETSc.setSizes([[nLocalNodes,None],[None,totalDofs]])
        MPETSc.createAIJ([[nLocalNodes,None],[None,totalDofs]])
        #MPETSc.setType('aij') # sparse
        # TODO: maybe change preallocation stuff
        MPETSc.setPreallocationNNZ([self.getPrealloc(False),
                                    self.getPrealloc(False)])
        #MPETSc.setPreallocationNNZ(0)
        # just slow down quietly if preallocation is insufficient
        MPETSc.setOption(PETSc.Mat.Option.NEW_NONZERO_ALLOCATION_ERR, False)
        # for debug:
        #MPETSc.setOption(PETSc.Mat.Option.NEW_NONZERO_ALLOCATION_ERR, True)
        MPETSc.setUp()

        offset = 0
        for field in range(0,self.getNFields()):
            x_nodes = self.V.tabulate_dof_coordinates()\
                            .reshape((-1,self.mesh.geometry().dim()))
            if(self.getNFields()>1):
                dofs = self.V.sub(field).dofmap().dofs()
            else:
                dofs = self.V.dofmap().dofs()
            for I in arange(0,len(dofs)):
                x = x_nodes[dofs[I]-Istart]
                matRow = dofs[I]
                nodesAndEvals = self.getNodesAndEvals(x,field)

                # Ideally, would use globalDof here for consistency,
                # but it is not very efficient as implemented
                #cols = array(nodesAndEvals,dtype=INDEX_TYPE)[:,0] + offset
                #rows = array([matRow,],dtype=INDEX_TYPE)
                #values = npTranspose(array(nodesAndEvals)[:,1:2])
                #MPETSc.setValues(rows,cols,values,addv=PETSc.InsertMode.INSERT)
                
                for i in range(0,len(nodesAndEvals)):
                    # Ideally, would use globalDof here for consistency,
                    # but it is not very efficient as implemented
                    MPETSc[matRow,nodesAndEvals[i][0]+offset]\
                        = nodesAndEvals[i][1]
                
            offset += self.getNcp(field)
            
        MPETSc.assemblyBegin()
        MPETSc.assemblyEnd()

        return PETScMatrix(MPETSc)

    # override default behavior to order unknowns according to what task's
    # FE mesh they overlap.  this will (hopefully) reduce communication
    # cost in the matrix--matrix multiplies
    def generatePermutation(self):

        """
        Generates a permutation of the IGA degrees of freedom that tries to
        ensure overlap of their parallel partitioning with that of the FE
        degrees of freedom, which are partitioned automatically based on the
        FE mesh.
        """
        
        func = Function(self.V)
        Istart, Iend = as_backend_type(func.vector()).vec().getOwnershipRange()
        nLocalNodes = Iend - Istart

        totalDofs = 0
        for i in range(0,self.getNFields()):
            totalDofs += self.getNcp(i)
        
        MPETSc = PETSc.Mat()
        MPETSc.createAIJ([[nLocalNodes,None],[None,totalDofs]])
        MPETSc.setPreallocationNNZ([self.getPrealloc(False),
                                    self.getPrealloc(False)])
        MPETSc.setOption(PETSc.Mat.Option.NEW_NONZERO_ALLOCATION_ERR, False)
        MPETSc.setUp()

        offset = 0
        for field in range(0,self.getNFields()):
            x_nodes = self.V.tabulate_dof_coordinates()\
                            .reshape((-1,self.mesh.geometry().dim()))
            if(self.getNFields()>1):
                dofs = self.V.sub(field).dofmap().dofs()
            else:
                dofs = self.V.dofmap().dofs()
            for I in arange(0,len(dofs)):
                x = x_nodes[dofs[I]-Istart]
                matRow = dofs[I]
                nodesAndEvals = self.getNodesAndEvals(x,field)

                #cols = array(nodesAndEvals,dtype=INDEX_TYPE)[:,0] + offset
                #rows = array([matRow,],dtype=INDEX_TYPE)
                #values = full((1,len(nodesAndEvals)),mpirank+1)
                #MPETSc.setValues(rows,cols,values,addv=PETSc.InsertMode.INSERT)
                
                for i in range(0,len(nodesAndEvals)):
                    MPETSc[matRow,nodesAndEvals[i][0]+offset]\
                        = mpirank+1 # need to avoid losing zeros...
                   
            offset += self.getNcp(field)

        MPETSc.assemblyBegin()
        MPETSc.assemblyEnd()
        
        MT = MPETSc.transpose(PETSc.Mat())
        Istart, Iend = MT.getOwnershipRange()
        nLocal = Iend - Istart
        partitionInts = zeros(nLocal,dtype=INDEX_TYPE)
        for i in arange(Istart,Iend):
            rowValues = MT.getRow(i)[0]
            iLocal = i - Istart
            modeValues = mode(rowValues)[0]
            if(len(modeValues) > 0):
                partitionInts[iLocal] = int(mode(rowValues).mode[0]-0.5)
            else:
                partitionInts[iLocal] = 0 # necessary?
        partitionIS = PETSc.IS()
        partitionIS.createGeneral(partitionInts)

        # kludgy, non-scalable solution:
        
        # all-gather the partition indices and apply argsort to their
        # underlying arrays
        bigIndices = argsort(partitionIS.allGather().getIndices())\
                     .astype(INDEX_TYPE)

        # note: index set sort method only sorts locally on each processor

        # note: output of argsort is what we want for MatPermute(); it
        # maps from indices in the sorted array, to indices in the original
        # unsorted array.  
        
        # use slices [Istart:Iend] of the result from argsort to create
        # a new IS that can be used as a petsc ordering
        retval = PETSc.IS()
        retval.createGeneral(bigIndices[Istart:Iend])
        
        return retval
        
# abstract class representing a scalar basis of functions on a manifold for
# which we assume that each point has unique coordinates.  
class AbstractScalarBasis(object):

    """
    Abstraction defining the behavior of a collection of scalar basis 
    functions, defined on a manifold for which each point has unique 
    coordinates.
    """
    
    __metaclass__ = abc.ABCMeta

    @abc.abstractmethod
    def getNodesAndEvals(self,xi):
        """
        Given a parametric point ``xi``, return a list of the form
        
        ``[[index0, N_index0(xi)], [index1,N_index1(xi)], ... ]``
        
        where ``N_i`` is the ``i``-th basis function.
        """
        return

    @abc.abstractmethod
    def getNcp(self):
        """
        Returns the total number of basis functions.
        """
        return

    @abc.abstractmethod
    def generateMesh(self):
        """
        Generates and returns an FE mesh sufficient for extracting this spline
        basis.
        """
        return

    @abc.abstractmethod
    def getDegree(self):
        """
        Returns a polynomial degree for FEs that is sufficient for extracting 
        this spline basis.
        """
        return

    #@abc.abstractmethod
    # assume DG unless this is overridden by a subclass (as DG will work even
    # if CG is okay (once they fix DG for quads/hexes at least...))
    def needsDG(self):
        """
        Returns a Boolean indicating whether or not DG elements are needed
        to represent this spline space (i.e., whether or not the basis is
        discontinuous).
        """
        return True

    @abc.abstractmethod
    def useRectangularElements(self):
        """
        Returns a Boolean indicating whether or not rectangular (i.e., quad
        or hex) elements should be used for extraction of this basis.
        """
        return
    
    #@abc.abstractmethod
    #def getParametricDimension(self):
    #    return

    # Override this in subclasses to optimize memory use.  It should return
    # the maximum number of IGA basis functions whose supports might contain
    # a finite element node (i.e, the maximum number of nonzero
    # entries in a row of M corrsponding to that FE basis function.)
    def getPrealloc(self):
        """
        Returns some upper bound on the number of nonzero entries per row
        of the extraction matrix for this spline space.  If this can be
        easily estimated for a specific spline type, then this method 
        should almost certainly be overriden by that subclass for memory
        efficiency, as the default value implemented in the abstract class is
        overkill.
        """
        return DEFAULT_PREALLOC
    
# interface needed for a control mesh with a coordinate chart
class AbstractControlMesh(object):
    """
    Abstraction representing the behavior of a control mesh, i.e., a mapping
    from parametric to physical space.
    """
    
    __metaclass__ = abc.ABCMeta

    @abc.abstractmethod
    def getHomogeneousCoordinate(self,node,direction):
        """
        Returns the ``direction``-th homogeneous component of the control 
        point with index ``node``.
        """
        return

    @abc.abstractmethod
    def getScalarSpline(self):
        """
        Returns the instance of ``AbstractScalarBasis`` that represents
        each homogeneous component of the control mapping.
        """
        return

    @abc.abstractmethod
    def getNsd(self):
        """
        Returns the dimension of physical space.
        """
        return


class AbstractMultiFieldSpline(AbstractCoordinateChartSpline):

    """
    Interface for a general multi-field spline.  The reason this is
    a special case of ``AbstractCoordinateChartSpline`` 
    (instead of being redundant in light of AbstractExtractionGenerator) 
    is that it uses a collection of ``AbstractScalarBasis`` objects, whose 
    ``getNodesAndEvals()`` methods require parametric coordinates 
    to correspond to unique points.
    """

    __metaclass__ = abc.ABCMeta

    @abc.abstractmethod
    def getControlMesh(self):
        """
        Returns some object implementing ``AbstractControlMesh``, that
        represents this spline's control mesh.
        """
        return

    @abc.abstractmethod
    def getFieldSpline(self,field):
        """
        Returns the ``field``-th unknown scalar field's 
        ``AbstractScalarBasis``.
        """
        return

    # overrides method inherited from AbstractExtractionGenerator, using
    # getPrealloc() methods from its AbstractScalarBasis members.
    def getPrealloc(self,control):
        if(control):
            retval = self.getScalarSpline(-1).getPrealloc()
        else:
            maxPrealloc = 0
            for i in range(0,self.getNFields()):
                prealloc = self.getScalarSpline(i).getPrealloc()
                if(prealloc > maxPrealloc):
                    maxPrealloc = prealloc
            retval = maxPrealloc
        #print control, retval
        return retval
    
    def getScalarSpline(self,field):
        """
        Returns the ``field``-th unknown scalar field's \
        ``AbstractScalarBasis``, or, if ``field==-1``, the 
        basis for the scalar space of the control mesh.
        """
        if(field==-1):
            return self.getControlMesh().getScalarSpline()
        else:
            return self.getFieldSpline(field)

    def getNsd(self):
        """
        Returns the dimension of physical space.
        """
        return self.getControlMesh().getNsd()

    def getHomogeneousCoordinate(self,node,direction):
        """
        Invokes the synonymous method of its control mesh.
        """
        return self.getControlMesh()\
            .getHomogeneousCoordinate(node,direction)

    def getNodesAndEvals(self,x,field):
        return self.getScalarSpline(field).getNodesAndEvals(x)

    def generateMesh(self):
        return self.getScalarSpline(-1).generateMesh()

    def getDegree(self,field):
        """
        Returns the polynomial degree needed to extract the ``field``-th
        unknown scalar field.
        """
        return self.getScalarSpline(field).getDegree()

    def getNcp(self,field):
        """
        Returns the number of degrees of freedom for a given ``field``.
        """
        return self.getScalarSpline(field).getNcp()

    def useDG(self):
        for i in range(-1,self.getNFields()):
            if(self.getScalarSpline(i).needsDG()):
                return True
        return False

# common case of all control functions and fields belonging to the
# same scalar space.  Note: fields are all stored in homogeneous format, i.e.,
# they need to be divided through by weight to get an iso-parametric
# formulation.
class EqualOrderSpline(AbstractMultiFieldSpline):
    """
    A concrete subclass of ``AbstractMultiFieldSpline`` to cover the common
    case of multi-field splines in which all unknown scalar fields are 
    discretized using the same ``AbstractScalarBasis``.
    """
    
    # args: numFields, controlMesh
    def customSetup(self,args):
        """
        ``args = (numFields,controlMesh)``, where ``numFields`` is the 
        number of unknown scalar fields and ``controlMesh`` is an
        ``AbstractControlMesh`` providing the mapping from parametric to
        physical space and, in this case, the scalar basis to be used for
        all unknown scalar fields.
        """
        self.numFields = args[0]
        self.controlMesh = args[1]
    def getNFields(self):
        return self.numFields
    def getControlMesh(self):
        return self.controlMesh
    def getFieldSpline(self,field):
        return self.getScalarSpline(-1)

    def addZeroDofsByLocation(self, subdomain, field):
        """
        Because, in the equal-order case, there is a one-to-one
        correspondence between the DoFs of the scalar fields and the
        control points of the geometrical mapping, one may, in some cases, 
        want to assign boundary conditions to the DoFs of the scalar fields
        based on the locations of their corresponding control points.  

        This method assigns homogeneous Dirichlet BCs to DoFs of a given
        ``field`` if the corresponding control points fall within 
        ``subdomain``, which is an instance of ``SubDomain``.
        """
        
        # this is prior to the permutation
        Istart, Iend = self.M_control.mat().getOwnershipRangeColumn()
        nsd = self.getNsd()
        # since this checks every single control point, it needs to
        # be scalable
        for I in arange(Istart, Iend):
            p = zeros(nsd+1)
            for j in arange(0,nsd+1):
                p[j] = self.getHomogeneousCoordinate(I,j)
            for j in arange(0,nsd):
                p[j] /= p[nsd]
            # make it strictly based on location, regardless of how the
            # on_boundary argument is handled
            isInside = subdomain.inside(p[0:nsd],False) \
                       or subdomain.inside(p[0:nsd],True)
            if(isInside):
                self.zeroDofs += [self.globalDof(field,I),]
    
# a concrete case with a list of distinct scalar splines
class FieldListSpline(AbstractMultiFieldSpline):

    """
    A concrete case of a multi-field spline that is constructed from a given
    list of ``AbstractScalarBasis`` objects.  
    """
    
    # args: controlMesh, fields
    def customSetup(self,args):
        """
        ``args = (controlMesh,fields)``, where ``controlMesh`` is an
        ``AbstractControlMesh`` providing the mapping from parametric to
        physical space and ``fields`` is a list of ``AbstractScalarBasis``
        objects for the unknown scalar fields.
        """
        self.controlMesh = args[0]
        self.fields = args[1]
    def getNFields(self):
        return len(self.fields)
    def getControlMesh(self):
        return self.controlMesh
    def getFieldSpline(self,field):
        return self.fields[field]