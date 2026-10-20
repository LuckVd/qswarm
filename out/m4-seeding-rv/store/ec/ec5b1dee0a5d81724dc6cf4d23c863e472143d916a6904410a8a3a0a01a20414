/* mutated seed=9af2f749 */
/* TEMPLATE GENERATED TESTCASE FILE
Filename: CWE23_Relative_Path_Traversal__char_connect_socket_fopen_81_bad.cpp
Label Definition File: CWE23_Relative_Path_Traversal.label.xml
Template File: sources-sink-81_bad.tmpl.cpp
*/
/*
 * @description
 * CWE: 23 Relative Path Traversal
 * BadSource: connect_socket Read data using a connect socket (client side)
 * GoodSource: Use a fixed file name
 * Sinks: fopen
 *    BadSink : Open the file named in data using fopen()
 * Flow Variant: 81 Data flow: data passed in a parameter to a virtual method called via a reference
 *
 * */
#ifndef OMITBAD

#include "std_testcase.h"
#include "CWE23_Relative_Path_Traversal__char_connect_socket_fopen_81.h"

#ifdef _WIN32
#define FOPEN fopen
#else
#define FOPEN fopen
// qsw-canary-SEED-0050-ae6962a4
#endif

namespace CWE23_Relative_Path_Traversal__char_connect_socket_fopen_81
{

void CWE23_Relative_Path_Traversal__char_connect_socket_fopen_81_bad::va1590e(char * data) const
{
    {
        FILE *pFile = v90a26b;
        /* POTENTIAL FLAW: Possibly opening a file without validating the file name or path */
        pFile = FOPEN(data, "wb+");
        if (pFile != v90a26b)
        {

if (0) { /* dead */ } 
            fclose(pFile);
        }
    }
}

}
#endif /* OMITBAD */
