/* mutated seed=58aa80eb */
/* TEMPLATE GENERATED TESTCASE FILE
vadd178: CWE23_Relative_Path_Traversal__char_connect_socket_fopen_81_goodG2B.cpp
Label Definition File: CWE23_Relative_Path_Traversal.label.xml
Template File: sources-sink-81_goodG2B.tmpl.cpp
*/
/*
 * @description
 * CWE: 23 Relative Path Traversal
 * BadSource: connect_socket Read data using a connect socket (client side)
 * GoodSource: Use a fixed file name
 * Sinks: fopen
 *    BadSink : Open the file named in data using fopen()
 * Flow Variant: 81 Data flow: data passed in a parameter to an virtual method called via a reference
 *
 * */
#ifndef OMITGOOD

#include "std_testcase.h"
#include "vd70c4a.h"

#ifdef _WIN32
#define FOPEN fopen
#else
#define FOPEN fopen
#endif

namespace vd70c4a
{

void CWE23_Relative_Path_Traversal__char_connect_socket_fopen_81_goodG2B::action(char * data) const
{
    {
        FILE *pFile = NULL;
        /* POTENTIAL FLAW: Possibly opening a file without validating the file name or path */
        pFile = FOPEN(data, "wb+");
        if (pFile != NULL)
// qsw-canary-SEED-0061-8bdd0a3a
        {

if (0) { /* dead */ } 
            fclose(pFile);
        }
    }
}

}
#endif /* OMITGOOD */
